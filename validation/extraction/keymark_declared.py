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

Air-to-air
----------
Those ``temperature`` selectors are what kept air-to-air out of the reference
population entirely: air-to-air certificates declare ``temperature`` 9 for
heating and 10 for cooling, so every one of them was dropped and the shipped
``keymark_en14825_declared.csv`` contains no Air/Air row. Pass
``--family air-to-air`` to read them instead, into a separate pair of files.

That population is 19 models from 3 manufacturers, against 9,162 models from
169 manufacturers for air-to-water. It is a reference, not a reference
*distribution* -- small enough that a p10-p90 band would be meaningless, which
is why the summary also carries ``cop_min`` / ``cop_max``.

Run
---
``uv run python -m validation.extraction.keymark_declared``
``uv run python -m validation.extraction.keymark_declared --family air-to-air``
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from ._pdftext import EVIDENCE, EvidenceMissing

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_DIR = EVIDENCE / "data" / "hplib" / "csv"
OUT_CSV = REPO_ROOT / "validation" / "data" / "keymark_en14825_declared.csv"
OUT_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"
OUT_A2A_CSV = REPO_ROOT / "validation" / "data" / "keymark_a2a_declared.csv"
OUT_A2A_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_a2a_summary.csv"

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

# ----------------------------------------------------------------------------
# Air-to-air
# ----------------------------------------------------------------------------
# The air-to-air certificates use the same EN 14825 heating codes as the
# air-to-water ones but a different ``temperature`` selector, so the filter
# above drops every one of them: air-to-air declares ``temperature`` 9 for
# heating and 10 for cooling, neither of which is in {4, 5, 6, 7}. That is why
# `keymark_en14825_declared.csv` contains no Air/Air row at all.
#
# They also carry a full cooling declaration, ``EN14825_030..039``, which
# nothing in this repository read before. For air-to-air that is the more
# natural of the two duties -- the nameplate is a cooling capacity -- so both
# are extracted here.
#
# Observed (climate, temperature) combinations across the thirteen Air/Air
# certificates: (3, 9) heating average climate, (1, 9) heating colder climate,
# (4, 10) cooling average climate. ``AVERAGE_CLIMATE`` already excludes the
# colder-climate rows, so the temperature selector alone separates the duties.

#: Air-to-air heating, average climate. Same codes and part loads as the
#: air-to-water heating points -- EN 14825 sets the part load from the outdoor
#: bin rule, which does not depend on what the sink is.
A2A_HEATING_POINTS = POINTS

#: Air-to-air cooling, average climate. ``EN14825_030`` is the design cooling
#: load and ``031`` the SEER; the four test points start at ``032``. Part loads
#: are the cooling bin rule ``(T_j - 16) / (35 - 16)``.
A2A_COOLING_POINTS = {
    "A": ("EN14825_032", "EN14825_033", 35.0, 1.00),
    "B": ("EN14825_034", "EN14825_035", 30.0, 0.74),
    "C": ("EN14825_036", "EN14825_037", 25.0, 0.47),
    "D": ("EN14825_038", "EN14825_039", 20.0, 0.21),
}

#: ``temperature`` selector per air-to-air duty.
A2A_APPLICATION = {"heating": {9}, "cooling": {10}}

#: Plausibility window on the declared efficiency. Cooling EER at the lightest
#: test point reaches 17.4 in this population, so the air-to-water bound of 15
#: would silently truncate the D point and bias the sample.
EFFICIENCY_BOUNDS = {"heating": (1.0, 15.0), "cooling": (1.0, 25.0)}


# A handful of certificates carry a very long free-text field; the default
# limit is not a data-integrity guard here, only a memory one.
csv.field_size_limit(10_000_000)


def _read_certificate(
    path: Path,
    *,
    applications: dict[str, set[int]] | None = None,
    points_for: dict[str, dict] | None = None,
    type_prefix: str | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> list[dict]:
    """One certificate file may hold several models; split on ``title`` rows.

    The keyword arguments select the equipment family. Their defaults reproduce
    the air-to-water extraction exactly; :func:`build` passes the air-to-air set
    when asked for it.

    ``applications`` maps an application name to the ``temperature`` codes that
    select it, ``points_for`` gives the test-point code map per application,
    ``type_prefix`` filters on the certificate's ``Type`` field, and ``bounds``
    is the plausibility window on the declared efficiency per application.
    """
    applications = applications if applications is not None else APPLICATION
    bounds = bounds if bounds is not None else {}

    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []

    general: dict[str, str] = {}
    for row in rows:
        if row.get("varName") in ("Manufacturer", "Modelname", "Refrigerant", "Type", "Date"):
            general.setdefault(row["varName"], row.get("value", ""))

    if type_prefix is not None and not general.get("Type", "").startswith(type_prefix):
        return []

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
        for application, codes in applications.items():
            points = points_for[application] if points_for is not None else POINTS
            lo_eff, hi_eff = bounds.get(application, (1.0, 15.0))
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
            for label, (p_code, cop_code, t_outdoor, plr) in points.items():
                cop = selected.get(cop_code)
                p_th = selected.get(p_code)
                if cop is None or p_th is None or not (lo_eff < cop < hi_eff) or p_th <= 0.0:
                    complete = False
                    break
                record[f"cop_{label}"] = cop
                record[f"p_th_{label}_kW"] = p_th
                record[f"t_out_{label}_C"] = t_outdoor
                record[f"plr_{label}"] = plr
            if complete:
                out.append(record)
    return out


def build(family: str = "air-to-water") -> pd.DataFrame:
    """Read every certificate and return the declared test points.

    ``family`` is ``"air-to-water"`` (the shipped reference population) or
    ``"air-to-air"``. The two write different files and are never pooled: they
    are different equipment classes measured against different sink boundary
    conditions, and the air-to-air population is two orders of magnitude
    smaller.
    """
    if not CSV_DIR.exists():
        raise EvidenceMissing(
            f"{CSV_DIR.relative_to(REPO_ROOT)} not found.\n"
            f"Obtain it from {SOURCE_URL} -- the repository archive's "
            "`input/csv` directory, copied under validation/evidence/data/hplib/."
        )
    if family == "air-to-water":
        kwargs: dict = {}
    elif family == "air-to-air":
        kwargs = {
            "applications": A2A_APPLICATION,
            "points_for": {"heating": A2A_HEATING_POINTS, "cooling": A2A_COOLING_POINTS},
            "type_prefix": "Air/Air",
            "bounds": EFFICIENCY_BOUNDS,
        }
    else:
        raise ValueError(f"unknown family {family!r}; expected air-to-water or air-to-air")

    records: list[dict] = []
    for path in sorted(CSV_DIR.glob("*.csv")):
        records.extend(_read_certificate(path, **kwargs))
    return pd.DataFrame(records).drop_duplicates(subset=["title", "application"])


def _summarise(df: pd.DataFrame, points_for: dict[str, dict]) -> list[dict]:
    """Per-application quantiles and the share of models that rise A->D."""
    rows = []
    for application, group in df.groupby("application"):
        points = points_for[application]
        labels = list(points)
        cops = {label: group[f"cop_{label}"] for label in labels}
        rises = np.ones(len(group), dtype=bool)
        for lo, hi in zip(labels, labels[1:], strict=False):
            rises &= cops[hi].to_numpy() > cops[lo].to_numpy()
        d_over_c = float((cops["D"] > cops["C"]).mean() * 100.0)
        print(f"  {application} ({len(group)} records)")
        print(f"    {'point':>7}{'T_out':>8}{'PLR':>7}{'p25':>9}{'median':>9}{'p75':>8}{'min':>8}{'max':>8}")
        for label in labels:
            _, _, t_out, plr = points[label]
            series = cops[label]
            print(
                f"    {label:>7}{t_out:>8.0f}{plr:>7.2f}"
                f"{series.quantile(0.25):>9.2f}{series.median():>9.2f}{series.quantile(0.75):>8.2f}"
                f"{series.min():>8.2f}{series.max():>8.2f}"
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
                    "cop_min": series.min(),
                    "cop_max": series.max(),
                }
            )
        print(f"    rises at every step, A->D : {rises.mean() * 100:.1f} % of models")
        print(f"    COP(D) > COP(C)           : {d_over_c:.1f} % of models")
        print()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        choices=("air-to-water", "air-to-air"),
        default="air-to-water",
        help="equipment class to extract (default: air-to-water)",
    )
    args = parser.parse_args()

    if args.family == "air-to-water":
        out_csv, out_summary = OUT_CSV, OUT_SUMMARY
        points_for = dict.fromkeys(APPLICATION, POINTS)
    else:
        out_csv, out_summary = OUT_A2A_CSV, OUT_A2A_SUMMARY
        points_for = {"heating": A2A_HEATING_POINTS, "cooling": A2A_COOLING_POINTS}

    print(f"Heat Pump Keymark declared performance -- EN 14825 average climate, {args.family}")
    try:
        df = build(args.family)
    except EvidenceMissing as exc:
        print(f"  [skip] {exc}")
        return
    if df.empty:
        print("  no records matched -- nothing written")
        return
    df.to_csv(out_csv, index=False)

    print(f"  records                : {len(df)} (model x application)")
    print(f"  distinct models        : {df.title.nunique()}")
    print(f"  manufacturers          : {df.manufacturer.nunique()}")
    print()

    rows = _summarise(df, points_for)
    pd.DataFrame(rows).to_csv(out_summary, index=False)

    if args.family == "air-to-water":
        print("  The expected roll-over is not there. Declared COP improves toward")
        print("  the light test points, because EN 14825 lowers the flow temperature")
        print("  as it lowers the load -- so the lift falls with the duty. The")
        print("  part-load penalty real equipment pays comes from the minimum")
        print("  modulation limit and from cycling, not from the compressor")
        print("  efficiency correlations.")
    else:
        print("  This population is small enough that it cannot carry a band: it is")
        print("  a reference, not a reference distribution. Report the individual")
        print("  declared trajectories and their min-max, never p10-p90.")
    print()
    print(f"  wrote {out_csv.relative_to(REPO_ROOT)}")
    print(f"  wrote {out_summary.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
