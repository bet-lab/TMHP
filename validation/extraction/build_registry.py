"""Regenerate validation/registry/sources.yaml from the local evidence tree."""

import hashlib
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
EV = REPO / "validation" / "evidence"
OUT = REPO / "validation" / "registry" / "sources.yaml"

# Documents the extraction scripts actually consume, with their public identity.
DESCRIBED = {
    "catalogs/alfalaval_tyrva_airsock_unit_coolers.pdf": dict(
        title="TYR-A air sock unit coolers",
        publisher="Alfa Laval",
        doc_id="ERC00369EN-1112",
        rating="EN 328 SC2 (R-404A, t0 -8 C, air 0 C, DT1 8 K, frosted)",
        used_by="en328_inversion",
        url="https://www.alfalaval.com/",
    ),
    "catalogs/guntner_ghf_unit_coolers.pdf": dict(
        title="GHF.2 high-efficiency unit coolers",
        publisher="Guentner",
        doc_id="GHF.2",
        rating="EN 328 SC2 (R-404A, t0 -8 C, DT1 8 K)",
        used_by="en328_inversion",
        url="https://www.guentner.com/",
    ),
    "catalogs/guntner_gacc_air_cooler_datasheet.pdf": dict(
        title="Cubic Compact GACC air coolers",
        publisher="Guentner",
        doc_id="GACC",
        rating="EN 328 SC2 (R-404A, t0 -8 C, DT1 8 K), 50 Hz and 60 Hz tables",
        used_by="en328_inversion",
        url="https://www.guentner.com/",
    ),
    "catalogs/gea_searle_coolers_condensing_units.pdf": dict(
        title="Cooler and Condensing Unit Ranges",
        publisher="GEA Searle",
        doc_id="-",
        rating="EN 328 SC2 (R-404A, t0 -8 C, air 0 C, DT1 8 K); series JG TEC NS KEC KMe KLe",
        used_by="en328_inversion",
        url="https://www.gea.com/",
    ),
    "catalogs/alfalaval_acq504_condenser_spec.pdf": dict(
        title="AlfaGreen air-cooled condensers AC / ACD / ACV",
        publisher="Alfa Laval",
        doc_id="ECR00011EN (2003)",
        rating="ENV 327 (R-404A, air 25 C, condensing 40 C, DT1 15 K), declared verbatim",
        used_by="env327_inversion",
        url="https://www.alfalaval.com/",
    ),
    "catalogs/luve_air_cooled_condensers.pdf": dict(
        title="Air-Cooled Condensers",
        publisher="LU-VE",
        doc_id="-",
        rating="ENV 327 (R-404A, air 25 C, condensing 40 C, DT1 15 K)",
        used_by="env327_inversion",
        url="https://www.luvegroup.com/",
    ),
    "catalogs/trane_precedent_hp_pkgp_prc003.pdf": dict(
        title="Packaged Heat Pump Units Precedent 3-10 Tons",
        publisher="Trane",
        doc_id="PKGP-PRC003-EN (05/06)",
        rating="AHRI 210/240 or 340/360 cooling: 95 F ambient, 80/67 F entering; R-22",
        used_by="trane_geometry (Tables 1-3, outdoor coil geometry and fan flow)",
        url="https://www.trane.com/",
    ),
    "catalogs/trane_precedent_hp_wsc048_catalog.pdf": dict(
        title="Packaged Rooftop Air Conditioners Precedent - Heat Pump 3-10 Tons 60 Hz",
        publisher="Trane",
        doc_id="PKGP-PRC013K-EN (02/16/2015)",
        rating="AHRI cooling: 95 F ambient, 80/67 F entering; R-410A",
        used_by="trane_geometry (Tables 1-3, outdoor coil geometry and fan flow)",
        url="https://www.trane.com/",
    ),
    "catalogs/trane_precedent_hp_2004_pkgp.pdf": dict(
        title="Packaged Heat Pumps (September 2004)",
        publisher="Trane",
        doc_id="PKGP-PRC003-EN (09/04)",
        rating="predecessor printing, cross-check only",
        used_by="cross-check",
        url="https://www.trane.com/",
    ),
    "catalogs/daikin_rxm_a_databook_eeden24.pdf": dict(
        title="RXM-A engineering data book (pair application)",
        publisher="Daikin",
        doc_id="EEDEN24-200",
        rating="EN 14511; cooling and heating grids at nominal operating frequency",
        used_by="daikin_grids -> validation/catalogs/daikin_rxm*.yaml",
        url="https://www.daikin.eu/",
    ),
    "catalogs/fujitsu_asuh09lpas_dtm.pdf": dict(
        title="ASUH/AOUH LPAS design & technical manual",
        publisher="Fujitsu General",
        doc_id="-",
        rating="capacity grids, SI blocks; heating grids at maximum capacity",
        used_by="fujitsu_grids -> validation/catalogs/fujitsu_asuh*.yaml",
        url="https://www.fujitsu-general.com/",
    ),
    "data/hplib/csv": dict(
        title="Heat Pump Keymark declared performance records (hplib input/csv)",
        publisher="Forschungszentrum Juelich (hplib), MIT licence",
        doc_id="doi:10.5281/zenodo.5521597",
        rating="EN 14825 average climate, test points A-D, low and medium application",
        used_by="keymark_declared -> validation/data/keymark_en14825_summary.csv",
        url="https://github.com/FZJ-IEK3-VSA/hplib",
    ),
    "pdfs/guth_atakan_2023_ijrefrig.pdf": dict(
        title="Semi-empirical model of a variable speed scroll compressor for R-290",
        publisher="Int. J. Refrigeration 146, 483-499",
        doc_id="doi:10.1016/j.ijrefrig.2022.10.024",
        rating="-",
        used_by="compressor_efficiency (Table A.3 electro-mechanical shape)",
        url="https://doi.org/10.1016/j.ijrefrig.2022.10.024",
    ),
    "data/ossorio2023/1-s2.0-S1359431123007548-mmc2.xlsx": dict(
        title="Inverter efficiency measurements, supplementary data mmc2",
        publisher="Applied Thermal Engineering 233, 120725 (CC BY)",
        doc_id="doi:10.1016/j.applthermaleng.2023.120725",
        rating="drive efficiency vs output frequency, 15-110 Hz, three inverters",
        used_by="compressor_speed_losses",
        url="https://doi.org/10.1016/j.applthermaleng.2023.120725",
    ),
    "pdfs/elson_vehr_2006_icec1772.pdf": dict(
        title="Scroll compressor performance at low speed",
        publisher="Purdue ICEC",
        doc_id="ICEC 1772 (2006)",
        rating="-",
        used_by="compressor efficiency evidence",
        url="https://docs.lib.purdue.edu/icec/",
    ),
    "pdfs/dk_energy_agency_scop_nef_en14825.pdf": dict(
        title="SCOP / EN 14825 guidance",
        publisher="Danish Energy Agency",
        doc_id="-",
        rating="EN 14825 test-point conditions (secondary source)",
        used_by="plr coordinate definition",
        url="https://ens.dk/",
    ),
    "pdfs/ehpa_testreg_AA-HP_v1.3a_2021.pdf": dict(
        title="Heat Pump Keymark testing regulation, air-to-air",
        publisher="EHPA",
        doc_id="TestReg AA-HP v1.3a (2021)",
        rating="EN 14825 test-point conditions (secondary source)",
        used_by="plr coordinate definition",
        url="https://www.ehpa.org/",
    ),
}


# Bulk datasets are recorded as one aggregate row rather than one row per file.
# The hplib export alone is 2,634 certificates; listing each would bury the
# fifteen documents a reader actually needs to obtain.
BULK_DIRS = {"data/hplib/csv": "Heat Pump Keymark certificates (hplib input/csv)"}


def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def pages(p):
    if p.suffix.lower() != ".pdf":
        return None
    try:
        out = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, timeout=20).stdout
        for l in out.splitlines():
            if l.startswith("Pages:"):
                return int(l.split(":", 1)[1])
    except Exception:
        return None
    return None


header = """# Provenance registry for validation/evidence/
#
# The evidence tree itself is NOT tracked: publisher PDFs, page renders of
# copyrighted papers and supplementary datasets are not redistributable. This
# file is the tracked half -- it records what each document is, where to obtain
# it, and the checksum of the copy the extraction scripts were run against.
#
# A reader who obtains the same document from the publisher and gets the same
# sha256 is running on identical bytes, and every number under validation/data/
# follows from those bytes by the scripts in validation/extraction/.
#
# Regenerate with: uv run python -m validation.extraction.build_registry

documents:
"""

lines = [header]
described, undescribed = [], []
bulk_counts: dict[str, tuple[int, int]] = {}
for p in sorted(EV.rglob("*")):
    if not p.is_file():
        continue
    rel = str(p.relative_to(EV))
    parent = next((d for d in BULK_DIRS if rel.startswith(d + "/")), None)
    if parent:
        count, size = bulk_counts.get(parent, (0, 0))
        bulk_counts[parent] = (count + 1, size + p.stat().st_size)
        continue
    (described if rel in DESCRIBED else undescribed).append((rel, p))

for rel, d in ((r, DESCRIBED[r]) for r in DESCRIBED if r in BULK_DIRS):
    count, size = bulk_counts.get(rel, (0, 0))
    if not count:
        continue
    lines.append(f"  - path: {rel}/\n")
    lines.append(f'    title: "{d["title"]}"\n')
    lines.append(f'    publisher: "{d["publisher"]}"\n')
    lines.append(f'    document_id: "{d["doc_id"]}"\n')
    lines.append(f'    rating_condition: "{d["rating"]}"\n')
    lines.append(f'    used_by: "{d["used_by"]}"\n')
    lines.append(f'    obtain_from: "{d["obtain_from"] if "obtain_from" in d else d["url"]}"\n')
    lines.append(f"    files: {count}\n")
    lines.append(f"    bytes: {size}\n")
    lines.append("\n")

for rel, p in described:
    d = DESCRIBED[rel]
    npages = pages(p)
    lines.append(f"  - path: {rel}\n")
    lines.append(f'    title: "{d["title"]}"\n')
    lines.append(f'    publisher: "{d["publisher"]}"\n')
    lines.append(f'    document_id: "{d["doc_id"]}"\n')
    lines.append(f'    rating_condition: "{d["rating"]}"\n')
    lines.append(f'    used_by: "{d["used_by"]}"\n')
    lines.append(f'    obtain_from: "{d["url"]}"\n')
    lines.append(f"    bytes: {p.stat().st_size}\n")
    if npages:
        lines.append(f"    pages: {npages}\n")
    lines.append(f'    sha256: "{sha(p)}"\n')
    lines.append("\n")

lines.append("# Archived but not yet consumed by an extraction script. Listed so the\n")
lines.append("# evidence tree stays auditable: a file with no reader is a file nobody\n")
lines.append("# has checked.\n")
lines.append("unused_archive:\n")
for rel, p in undescribed:
    lines.append(f'  - path: {rel}\n    bytes: {p.stat().st_size}\n    sha256: "{sha(p)}"\n')

OUT.write_text("".join(lines))
print(f"wrote {OUT} -- {len(described)} described, {len(undescribed)} archived")
