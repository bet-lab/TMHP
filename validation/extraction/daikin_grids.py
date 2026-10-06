"""Transcribe Daikin split-unit capacity grids into parity catalogues.

Why these documents
-------------------
TMHP's ``AirSourceHeatPump`` is one outdoor unit serving one indoor unit. A
VRF catalogue does not match that structure -- its published performance is
indexed by combination ratio, and mapping combination ratio to part load
requires an assumption about the indoor coil that would go straight into the
comparison. Daikin's pair-application engineering data books are the right
shape: one outdoor unit, one indoor unit, no combination or pipe-length
correction, and a published grid of total capacity and power input against
outdoor and indoor temperature.

The latent boundary
-------------------
In cooling these tables publish total capacity ``TC`` and sensible capacity
``SHC``; the difference is dehumidification. TMHP's indoor coil is a dry
sensible-heat exchanger, so a cooling row with a low sensible heat ratio asks
the model to carry a latent duty it does not model. Every cooling point is
therefore tagged with its sensible heat ratio so the comparison can be read --
and the headline claim made -- on rows where the coil is nearly dry. Heating
rows have no latent component and need no such qualification.

Run
---
``uv run python -m validation.extraction.daikin_grids``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._pdftext import EvidenceMissing, text_lines

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "validation" / "catalogs"


@dataclass(frozen=True)
class Databook:
    pdf: str
    document: str
    refrigerant: str
    manufacturer: str
    first_page: int
    last_page: int
    unit_pattern: str


DATABOOKS = (
    Databook(
        pdf="catalogs/daikin_rxm_a_databook_eeden24.pdf",
        document="Daikin RXM-A engineering data book, EEDEN24-200 (R-32, pair application)",
        refrigerant="R32",
        manufacturer="Daikin",
        first_page=25,
        last_page=31,
        unit_pattern=r"^(FTXM\d+A)\s*/\s*(RXM\d+A)$",
    ),
    Databook(
        pdf="catalogs/daikin_rxm_m_databook_eeden16.pdf",
        document="Daikin RXM-M engineering data book, EEDEN16 (R-32, pair application)",
        refrigerant="R32",
        manufacturer="Daikin",
        first_page=0,
        last_page=200,
        unit_pattern=r"^(FTXM\d+[A-Z]?)\s*/\s*(RXM\d+[A-Z]?)$",
    ),
)

_NUM = r"-?\d+(?:,\d+)?"


def _f(token: str) -> float:
    return float(token.replace(",", "."))


def _parse_header_temps(line: str) -> list[float]:
    return [_f(t) for t in re.findall(rf"(?<![\d,]){_NUM}(?![\d,]*[A-Za-z])", line)]


def parse_databook(book: Databook) -> dict[str, dict]:
    """Return ``{unit: {"cooling": [...], "heating": [...]}}``."""
    units: dict[str, dict] = {}
    unit = None
    mode = None
    outdoor: list[float] = []
    page = book.first_page
    while page <= book.last_page:
        try:
            lines = text_lines(book.pdf, page)
        except (IndexError, ValueError):
            break
        except Exception:
            break
        for raw in lines:
            line = raw.strip()
            m = re.match(book.unit_pattern, line)
            if m:
                unit = f"{m.group(1)} / {m.group(2)}"
                units.setdefault(unit, {"cooling": [], "heating": []})
                mode, outdoor = None, []
                continue
            if unit is None:
                continue
            # A page-margin section number can precede the table title, so the
            # title is matched anywhere in the line rather than at its start.
            if re.search(r"\bCooling\b.*\d+\s*Hz", line):
                mode, outdoor = "cooling", []
                continue
            if re.search(r"\bHeating\b.*\d+\s*Hz", line):
                mode, outdoor = "heating", []
                continue
            # Each grid is captioned *after* its rows. The second heating grid
            # on every unit's page is taken at maximum operating frequency --
            # a forced condition, not the rated one -- so reading past this
            # caption would mix two different operating points into one set.
            if "nominal operating frequency" in line:
                mode, outdoor = None, []
                continue
            if "maximum operating frequency" in line:
                mode, outdoor = None, []
                continue
            if mode and line.startswith("Outdoor temperature"):
                outdoor = []
                continue
            if mode and not outdoor and re.fullmatch(rf"(?:{_NUM}\s+)+{_NUM}", line):
                outdoor = _parse_header_temps(line)
                continue
            if not (mode and outdoor):
                continue

            if mode == "cooling":
                m = re.match(rf"^({_NUM})\s+({_NUM})\s+((?:{_NUM}\s+)*{_NUM})$", line)
                if not m:
                    continue
                values = [_f(v) for v in m.group(3).split()]
                if len(values) != 3 * len(outdoor):
                    continue
                wb, db = _f(m.group(1)), _f(m.group(2))
                for i, t_out in enumerate(outdoor):
                    tc, shc, pi = values[3 * i : 3 * i + 3]
                    if pi <= 0.0:
                        continue
                    units[unit]["cooling"].append(
                        {"t_out": t_out, "t_in_db": db, "t_in_wb": wb, "tc": tc, "shc": shc, "pi": pi}
                    )
            else:
                m = re.match(rf"^({_NUM})\s+((?:{_NUM}\s+)*{_NUM})$", line)
                if not m:
                    continue
                values = [_f(v) for v in m.group(2).split()]
                if len(values) != 2 * len(outdoor):
                    continue
                db = _f(m.group(1))
                for i, t_out in enumerate(outdoor):
                    tc, pi = values[2 * i : 2 * i + 2]
                    if pi <= 0.0:
                        continue
                    units[unit]["heating"].append({"t_out": t_out, "t_in_db": db, "tc": tc, "pi": pi})
        page += 1
    return units


#: Nameplate cooling capacity [kW] from the specifications table, keyed by
#: outdoor unit. Read verbatim; the grid does not repeat it.
NOMINAL_COOLING_KW = {
    "RXM20A": 2.00,
    "RXM25A": 2.50,
    "RXM35A": 3.50,
    "RXM42A": 4.20,
    "RXM50A": 5.00,
}
#: Rated indoor air flow [m3/min] printed above each unit's grid as ``AFR``.
INDOOR_AIR_FLOW_M3_MIN = {
    "RXM20A": 10.3,
    "RXM25A": 11.9,
    "RXM35A": 12.4,
    "RXM42A": 13.4,
    "RXM50A": 14.0,
}

#: One printed row per document, asserted before the scrape is trusted.
ANCHORS = {
    "catalogs/daikin_rxm_a_databook_eeden24.pdf": (
        "FTXM20A / RXM20A",
        "cooling",
        {"t_out": 20.0, "t_in_db": 20.0, "t_in_wb": 14.0, "tc": 2.05, "shc": 1.90, "pi": 0.29},
    ),
}

#: Cooling rows below this sensible heat ratio ask the model to carry a latent
#: duty it does not compute. They are written out, tagged, and excluded from
#: the headline cooling claim.
DRY_COIL_SHR = 0.95


def write_catalogs(book: Databook, units: dict[str, dict]) -> list[str]:
    written = []
    for unit, modes in units.items():
        indoor, outdoor_unit = (part.strip() for part in unit.split("/"))
        nominal = NOMINAL_COOLING_KW.get(outdoor_unit)
        if nominal is None:
            continue
        slug = f"daikin_{outdoor_unit.lower()}"
        lines = []
        point_id = 0
        for row in modes["cooling"]:
            point_id += 1
            shr = row["shc"] / row["tc"]
            lines.append(
                f"  - {{id: {point_id}, t_source_C: {row['t_out']:.0f}, "
                f"t_sink_C: {row['t_in_db']:.0f}, q_kW: {row['tc']:.2f}, "
                f"power_kW: {row['pi']:.2f}, mode: cooling, "
                f'note: "WB {row["t_in_wb"]:.0f} C, SHR {shr:.2f}'
                f'{"" if shr >= DRY_COIL_SHR else ", wet coil"}"}}'
            )
        for row in modes["heating"]:
            point_id += 1
            lines.append(
                f"  - {{id: {point_id}, t_source_C: {row['t_out']:.0f}, "
                f"t_sink_C: {row['t_in_db']:.0f}, q_kW: {row['tc']:.2f}, "
                f"power_kW: {row['pi']:.2f}, mode: heating, "
                f'note: "outdoor WB"}}'
            )
        afr = INDOOR_AIR_FLOW_M3_MIN.get(outdoor_unit)
        header = f"""# {unit} -- Daikin pair application, {book.refrigerant}, {nominal:.2f} kW nominal cooling.
#
# One outdoor unit serving one indoor unit, which is the structure
# `AirSourceHeatPump` models. No combination-ratio or pipe-length correction
# applies, so the published grid can be compared directly.
#
# Power input is the whole system measured to EN 14511, and the capacities are
# net of indoor fan motor heat, both as stated in the document's notes.
#
# Cooling rows carry their sensible heat ratio in `note`. TMHP's indoor coil is
# a dry sensible exchanger, so rows below SHR {DRY_COIL_SHR:.2f} ask it to carry a latent
# duty it does not compute; those are reported separately rather than dropped.
#
# No compressor displacement is published for these units -- Daikin does not
# print one and its compressor division has no public catalogue for the swing
# machines used here. That makes this set a direct test of the derived
# displacement rule rather than of a specification input.
slug: {slug}
name: {unit}
manufacturer: {book.manufacturer}
model_class: ASHP
refrigerant: {book.refrigerant}
nominal_capacity_kW: {nominal:.2f}
cop_definition: "TC / PI, EN 14511 system power including the indoor fan"
source:
  document: "{book.document}"
  table: "Capacity tables, cooling and heating at nominal operating frequency"
published_inputs:
  rated_indoor_air_flow_m3_s: {afr / 60.0:.4f}
notes: >-
  The heating grid's outdoor axis is wet bulb, the cooling grid's is dry bulb,
  as printed. TMHP takes a single outdoor air temperature, so the heating
  points are evaluated at the published wet-bulb value; at the low humidity of
  cold weather the two are close, and the difference is noted rather than
  corrected.
points:
"""
        (CATALOG_DIR / f"{slug}.yaml").write_text(header + "\n".join(lines) + "\n")
        written.append(f"{slug} ({len(lines)} points)")
    return written


def main() -> None:
    print("Daikin pair-application capacity grids")
    for book in DATABOOKS:
        try:
            units = parse_databook(book)
        except EvidenceMissing as exc:
            print(f"  [skip] {book.pdf}: {exc}")
            continue
        anchor = ANCHORS.get(book.pdf)
        if anchor:
            unit, mode, expected = anchor
            assert expected in units.get(unit, {}).get(mode, []), (
                f"{book.pdf}: anchor row for {unit} {mode} not reproduced -- {expected}"
            )
        for unit, modes in units.items():
            n_cool, n_heat = len(modes["cooling"]), len(modes["heating"])
            if n_cool + n_heat == 0:
                continue
            print(f"  {unit:<24} cooling {n_cool:>4}  heating {n_heat:>4}")
        for line in write_catalogs(book, units):
            print(f"    wrote validation/catalogs/{line}")


if __name__ == "__main__":
    main()
