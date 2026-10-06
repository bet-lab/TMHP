"""The catalogue file format, and what may and may not go in it.

One YAML file per outdoor unit under ``validation/catalogs/``. The schema is
deliberately narrow: it can express what a manufacturer publishes and it cannot
express a tuned parameter, because the moment a catalogue file can carry a
free coefficient the parity plot stops meaning anything.

Fields
------
``slug``
    File-name stem; also the results file name.
``model_class``
    ``ASHPB`` (air-to-water, nameplate is a heating capacity) or ``ASHP``
    (air-to-air, nameplate is a cooling capacity).
``refrigerant``
    CoolProp fluid name, or a mixture specification for a blend CoolProp does
    not know by name.
``nominal_capacity_kW``
    The nameplate figure, as printed.
``published_inputs``
    Values the manufacturer publishes *about this machine*. Allowed:
    ``displacement_cc``, ``rated_air_flow_m3_s``, ``rated_indoor_air_flow_m3_s``.
    These are specification inputs, not tuning -- the same status as the
    nameplate capacity. Anything absent falls back to the library default,
    which is the case the derivation work is really about.
``sink_offset_K``
    For ASHPB only: how far the tank temperature sits below the catalogue's
    leaving water temperature. A statement about where the manufacturer
    measures, not a fitted parameter.
``points``
    The operating grid. ``t_source_C`` is outdoor air; ``t_sink_C`` is leaving
    water (ASHPB) or indoor dry bulb (ASHP); ``q_kW`` is the published duty;
    ``cop`` and/or ``power_kW`` are the published targets.
``mode``
    ``heating`` or ``cooling``; may be set per point.

Deliberately absent: every efficiency coefficient, every UA, every approach
temperature. If a unit cannot be matched without one of those, that is a
finding to report, not a field to add.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "validation" / "catalogs"

ALLOWED_PUBLISHED_INPUTS = frozenset(
    {"displacement_cc", "rated_air_flow_m3_s", "rated_indoor_air_flow_m3_s", "rated_indoor_air_flow_heating_m3_s"}
)
#: Rating standards a catalogue may declare. They define the test conditions
#: and the COP boundary (net vs gross capacity, indoor fan heat), so results are
#: never pooled across them -- ``run.py`` reports one headline per standard.
RATING_STANDARDS = frozenset({"EN 14511", "AHRI 210/240", "ISO 5151", "unknown"})
#: ``adopted`` catalogues form the validation set; ``hold`` catalogues are still
#: run and written (they are diagnostics) but never enter a headline figure.
CATALOG_STATUSES = frozenset({"adopted", "hold"})


@dataclass(frozen=True)
class OperatingPoint:
    id: int
    t_source_C: float
    t_sink_C: float
    q_kW: float
    cop: float | None = None
    power_kW: float | None = None
    mode: str = "heating"
    note: str = ""

    def target_cop(self) -> float:
        if self.cop is not None:
            return float(self.cop)
        if self.power_kW:
            return float(self.q_kW / self.power_kW)
        raise ValueError(f"point {self.id}: neither cop nor power_kW given")


@dataclass(frozen=True)
class Catalog:
    slug: str
    name: str
    manufacturer: str
    model_class: str
    refrigerant: str
    nominal_capacity_kW: float
    points: tuple[OperatingPoint, ...]
    source: dict[str, Any] = field(default_factory=dict)
    published_inputs: dict[str, Any] = field(default_factory=dict)
    sink_offset_K: float = 0.0
    cop_definition: str = ""
    notes: str = ""
    #: Test standard the published grid was rated under (see RATING_STANDARDS).
    rating_standard: str = "unknown"
    #: ``adopted`` or ``hold`` (see CATALOG_STATUSES); ``hold_reason`` says why.
    status: str = "adopted"
    hold_reason: str = ""

    def __post_init__(self) -> None:
        if self.model_class not in ("ASHP", "ASHPB"):
            raise ValueError(f"{self.slug}: model_class must be ASHP or ASHPB")
        if self.rating_standard not in RATING_STANDARDS:
            raise ValueError(f"{self.slug}: rating_standard must be one of {sorted(RATING_STANDARDS)}")
        if self.status not in CATALOG_STATUSES:
            raise ValueError(f"{self.slug}: status must be one of {sorted(CATALOG_STATUSES)}")
        if self.status == "hold" and not self.hold_reason:
            raise ValueError(f"{self.slug}: a held catalogue must say why (hold_reason)")
        unknown = set(self.published_inputs) - ALLOWED_PUBLISHED_INPUTS
        if unknown:
            raise ValueError(
                f"{self.slug}: published_inputs may only carry manufacturer "
                f"specifications {sorted(ALLOWED_PUBLISHED_INPUTS)}; got {sorted(unknown)}. "
                "A tuned coefficient does not belong in a validation catalogue."
            )


def load(path: Path) -> Catalog:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    points = tuple(OperatingPoint(**p) for p in raw.pop("points"))
    return Catalog(points=points, **raw)


def load_all(slug: str | None = None, *, include_hold: bool = True) -> list[Catalog]:
    paths = sorted(CATALOG_DIR.glob("*.yaml"))
    if slug:
        paths = [p for p in paths if p.stem == slug]
        if not paths:
            available = ", ".join(sorted(p.stem for p in CATALOG_DIR.glob("*.yaml")))
            raise SystemExit(f"no catalogue {slug!r}; available: {available or '(none)'}")
    catalogs = [load(p) for p in paths]
    if not include_hold:
        catalogs = [c for c in catalogs if c.status == "adopted"]
    return catalogs
