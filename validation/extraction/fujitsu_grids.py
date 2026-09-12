"""Transcribe Fujitsu split-unit capacity grids into parity catalogues.

Why this document
-----------------
The air-to-air side of the validation set would otherwise be a single
refrigerant. Daikin's pair-application range is entirely R-32; Fujitsu's ASUH
LPAS series is R-410A in the same size class and the same one-outdoor-to-one-
indoor structure, so it is what makes the air-to-air panel a test of the
refrigerant-independent physics rather than of one fluid.

The design and technical manual prints each grid twice, in imperial units and
then in SI. The SI block is parsed: same numbers, no conversion to get wrong.

One caveat carried forward
--------------------------
The heating grids are headed "values mentioned in the table are calculated
based on the maximum capacity". They are a published operating point with a
capacity and a power, so the comparison is valid -- but it is the machine at
full output, not at a rated condition, and the compressor is near the top of
its speed range throughout. That is noted in the generated catalogue rather
than quietly averaged in with the Daikin heating points.

Run
---
``uv run python -m validation.extraction.fujitsu_grids``
"""

from __future__ import annotations

import re
from pathlib import Path

from ._pdftext import EvidenceMissing, text_lines

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "validation" / "catalogs"

PDF = "catalogs/fujitsu_asuh09lpas_dtm.pdf"
DOCUMENT = "Fujitsu General ASUH/AOUH LPAS design & technical manual (R-410A)"
PAGES = range(10, 14)

#: Nominal cooling capacity [kW] from the specifications table.
NOMINAL_COOLING_KW = {"ASUH09LPAS": 2.64, "ASUH12LPAS": 3.52}

#: Cooling rows below this sensible heat ratio carry a latent duty TMHP does
#: not compute; they are tagged, not dropped.
DRY_COIL_SHR = 0.95

_MODEL = re.compile(r"Model:\s*(ASUH\d+LPAS)")
_SI_FLOW = re.compile(r"AFR\s*m3/h\s*(\d+)")
_TEMP_ROW = re.compile(r"^°CDB\s+((?:-?[\d.]+\s+)*-?[\d.]+)$")
_NUM = r"-?\d+(?:\.\d+)?"


def parse() -> dict[str, dict]:
    units: dict[str, dict] = {}
    unit = None
    mode = None
    indoor_db: list[float] = []
    flow_m3h: float | None = None
    si_block = False

    for page in PAGES:
        for raw in text_lines(PDF, page):
            line = raw.strip()

            if "Cooling capacity" in line and line.startswith("4-1"):
                mode = "cooling"
                continue
            if "Heating capacity" in line and line.startswith("4-2"):
                mode = "heating"
                continue

            m = _MODEL.search(line)
            if m:
                unit = m.group(1)
                units.setdefault(unit, {"cooling": [], "heating": [], "flow_m3h": {}})
                si_block = False
                indoor_db = []
                continue

            m = _SI_FLOW.search(line)
            if m and unit:
                # Everything after this line on this unit is the SI table.
                si_block = True
                flow_m3h = float(m.group(1))
                units[unit]["flow_m3h"][mode] = flow_m3h
                indoor_db = []
                continue

            if not (unit and mode and si_block):
                continue

            m = _TEMP_ROW.match(line)
            if m:
                indoor_db = [float(v) for v in m.group(1).split()]
                continue
            if not indoor_db:
                continue

            if mode == "cooling":
                m = re.match(rf"^({_NUM})\s+((?:{_NUM}\s+)*{_NUM})$", line)
                if not m:
                    continue
                values = [float(v) for v in m.group(2).split()]
                if len(values) != 3 * len(indoor_db):
                    continue
                t_out = float(m.group(1))
                for i, t_in in enumerate(indoor_db):
                    tc, shc, ip = values[3 * i : 3 * i + 3]
                    if ip <= 0.0:
                        continue
                    units[unit]["cooling"].append({"t_out": t_out, "t_in": t_in, "tc": tc, "shc": shc, "ip": ip})
            else:
                # Heating rows carry outdoor dry *and* wet bulb before the data.
                m = re.match(rf"^({_NUM})\s+({_NUM})\s+((?:{_NUM}\s+)*{_NUM})$", line)
                if not m:
                    continue
                values = [float(v) for v in m.group(3).split()]
                if len(values) != 2 * len(indoor_db):
                    continue
                t_out = float(m.group(1))
                for i, t_in in enumerate(indoor_db):
                    tc, ip = values[2 * i : 2 * i + 2]
                    if ip <= 0.0:
                        continue
                    units[unit]["heating"].append({"t_out": t_out, "t_in": t_in, "tc": tc, "ip": ip})
    return units


def write_catalogs(units: dict[str, dict]) -> list[str]:
    written = []
    for unit, data in units.items():
        nominal = NOMINAL_COOLING_KW.get(unit)
        if nominal is None or not (data["cooling"] or data["heating"]):
            continue
        slug = f"fujitsu_{unit.lower()}"
        lines = []
        point_id = 0
        for row in data["cooling"]:
            point_id += 1
            shr = row["shc"] / row["tc"]
            lines.append(
                f"  - {{id: {point_id}, t_source_C: {row['t_out']:.1f}, "
                f"t_sink_C: {row['t_in']:.1f}, q_kW: {row['tc']:.2f}, "
                f"power_kW: {row['ip']:.2f}, mode: cooling, "
                f'note: "SHR {shr:.2f}{"" if shr >= DRY_COIL_SHR else ", wet coil"}"}}'
            )
        for row in data["heating"]:
            point_id += 1
            lines.append(
                f"  - {{id: {point_id}, t_source_C: {row['t_out']:.1f}, "
                f"t_sink_C: {row['t_in']:.1f}, q_kW: {row['tc']:.2f}, "
                f"power_kW: {row['ip']:.2f}, mode: heating, "
                f'note: "maximum capacity"}}'
            )
        indoor_flow = data["flow_m3h"].get("cooling") or next(iter(data["flow_m3h"].values()))
        header = f"""# {unit} -- Fujitsu ASUH/AOUH LPAS, R-410A, {nominal:.2f} kW nominal cooling.
#
# One outdoor unit serving one indoor unit, which is the structure
# `AirSourceHeatPump` models. This is the air-to-air set's second refrigerant:
# without it that panel would be entirely R-32 and would say nothing about
# whether the physics carries across working fluids.
#
# Parsed from the manual's SI tables rather than its imperial ones -- the same
# numbers, with no unit conversion to get wrong.
#
# Heating points are taken at *maximum* capacity, as the manual states, so the
# compressor sits near the top of its speed range throughout that half of the
# grid. Cooling rows carry their sensible heat ratio; TMHP's indoor coil is a
# dry sensible exchanger, so rows below SHR {DRY_COIL_SHR:.2f} ask it to carry a latent duty
# it does not compute.
#
# No compressor displacement is published, so this unit exercises the derived
# displacement rule rather than a specification input.
slug: {slug}
name: {unit}
manufacturer: Fujitsu
model_class: ASHP
refrigerant: R410A
nominal_capacity_kW: {nominal:.2f}
cop_definition: "TC / IP, total capacity over input power"
source:
  document: "{DOCUMENT}"
  table: "Section 4, capacity tables (SI blocks)"
published_inputs:
  rated_indoor_air_flow_m3_s: {indoor_flow / 3600.0:.4f}
points:
"""
        (CATALOG_DIR / f"{slug}.yaml").write_text(header + "\n".join(lines) + "\n")
        written.append(f"{slug} ({len(lines)} points)")
    return written


def main() -> None:
    print("Fujitsu ASUH LPAS capacity grids")
    try:
        units = parse()
    except EvidenceMissing as exc:
        print(f"  [skip] {exc}")
        return
    # Verbatim check against the printed SI table before trusting the rest.
    anchor = {"t_out": -10.0, "t_in": 17.8, "tc": 2.51, "shc": 1.67, "ip": 0.31}
    assert anchor in units.get("ASUH09LPAS", {}).get("cooling", []), (
        "Fujitsu anchor row not reproduced -- expected ASUH09LPAS cooling at -10 / 17.8 degC: 2.51 / 1.67 / 0.31 kW"
    )
    for unit, data in units.items():
        print(f"  {unit:<16} cooling {len(data['cooling']):>4}  heating {len(data['heating']):>4}")
    for line in write_catalogs(units):
        print(f"    wrote validation/catalogs/{line}")


if __name__ == "__main__":
    main()
