"""Normalised record for one published compressor operating point.

One row = one printed (or published-model-generated) point of a *standalone*
compressor: what the manufacturer or the experimenter measured, plus the
conventions needed to reproduce the thermodynamic state.  Nothing derived
(pressure ratio, densities, efficiencies) lives here -- see ``derive``.
"""

from __future__ import annotations

import csv
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "validation" / "data" / "compressor_maps"
EVIDENCE_DIR = REPO_ROOT / "validation" / "evidence" / "compressor_maps"

COMP_TYPES = {"scroll", "rotary_single", "rotary_twin", "reciprocating"}
N_RATED_BASES = {"nameplate", "rating_row", "nominal_statement", "unresolved"}
SAT_CONVENTIONS = {"dew_dew", "dew_mid", "bubble_dew", "pressure"}
SH_CONVENTIONS = {"ashrae23", "en12900", "rg20", "measured", "custom"}
METHODS = {"pdf_text", "web_json", "web_table", "manual", "published_model"}


@dataclass(frozen=True)
class CompressorPoint:
    source_id: str  # short id, matches validation/registry/sources.yaml or a DOI
    manufacturer: str
    model: str
    comp_type: str
    refrigerant: str  # CoolProp fluid string
    V_disp_cm3: float
    N_rated_rps: float
    N_rated_basis: str
    N_rps: float
    Q_evap_W: float | None
    P_el_W: float | None
    T_evap_C: float | None = None
    T_cond_C: float | None = None
    p_suc_Pa: float | None = None  # when the source prints pressures instead of temperatures
    p_dis_Pa: float | None = None
    sat_convention: str = "dew_dew"
    sh_convention: str = "custom"
    dT_sh_K: float | None = None
    T_suc_C: float | None = None  # measured suction temperature (overrides dT_sh_K)
    dT_sc_K: float | None = None
    m_dot_kg_s: float | None = None
    T_dis_C: float | None = None
    Q_cond_W: float | None = None
    P_includes_inverter: bool | None = None
    speed_variant: str = ""  # e.g. OPI 'V1'/'V2'/'V4'
    method: str = "pdf_text"
    doc_path: str = ""
    page: str = ""
    table_id: str = ""
    note: str = ""
    weight: float = 1.0
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.comp_type not in COMP_TYPES:
            raise ValueError(f"comp_type {self.comp_type!r} not in {sorted(COMP_TYPES)}")
        if self.N_rated_basis not in N_RATED_BASES:
            raise ValueError(f"N_rated_basis {self.N_rated_basis!r}")
        if self.sat_convention not in SAT_CONVENTIONS:
            raise ValueError(f"sat_convention {self.sat_convention!r}")
        if self.sh_convention not in SH_CONVENTIONS:
            raise ValueError(f"sh_convention {self.sh_convention!r}")
        if self.method not in METHODS:
            raise ValueError(f"method {self.method!r}")
        if (self.T_evap_C is None or self.T_cond_C is None) and (self.p_suc_Pa is None or self.p_dis_Pa is None):
            raise ValueError("need either (T_evap_C, T_cond_C) or (p_suc_Pa, p_dis_Pa)")
        if self.Q_evap_W is None and self.m_dot_kg_s is None:
            raise ValueError("need Q_evap_W or m_dot_kg_s")

    @property
    def n_star(self) -> float:
        return self.N_rps / self.N_rated_rps


COLUMNS = [f.name for f in fields(CompressorPoint) if f.name != "extra"]


def write_points(points: list[CompressorPoint], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for p in points:
            d = asdict(p)
            d.pop("extra", None)
            w.writerow(d)


def read_points(path: Path) -> list[CompressorPoint]:
    out: list[CompressorPoint] = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            kw: dict = {}
            for f in fields(CompressorPoint):
                if f.name == "extra":
                    continue
                v = row.get(f.name, "")
                if v == "" or v == "None":
                    if f.name in ("V_disp_cm3", "N_rated_rps", "N_rps"):
                        raise ValueError(f"missing {f.name} in {path}")
                    # optional numeric columns (Q_evap_W, P_el_W) have no default: blank means None
                    kw[f.name] = None if f.default is MISSING else f.default
                elif f.type in ("float", "float | None"):
                    kw[f.name] = float(v)
                elif f.type in ("bool | None",):
                    kw[f.name] = v == "True"
                else:
                    kw[f.name] = v
            out.append(CompressorPoint(**kw))
    return out
