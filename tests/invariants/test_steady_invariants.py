"""Steady-state physics invariants on a small grid over every cycle-resolved family.

Grid: family x refrigerant (R32, R290, R410A, R134a) x 3 source temperatures
x 2 demand temperatures x 2 part-load ratios (of the default ``hp_capacity``).
Every result is checked by :func:`._checks.check_steady`:

* reported as running (``hp_is_on``, ``converged``, ``failure_reason='none'``):
  cycle energy balance (0.5 %), COP_ref <= Carnot COP at the saturation
  temperatures, 0 < eta_is, eta_v, eta_em <= 1, T_evap_sat below the stream
  entering the evaporator, T_cond_sat above the stream entering the condenser,
  suction no hotter than the source stream, superheat and subcooling >= 0,
  finite discharge temperature;
* reported as off / failed / unconverged: no heat and no power, and a
  ``failure_reason`` other than ``'none'``;
* minimum-speed clamp: delivered duty <= max(request, rated capacity).

Grid points chosen keep every measured margin away from the check tolerance
(>= 0.3 K, >= 0.001 on efficiencies) so the outcome does not depend on the
platform. Two otherwise-selected points sat within 0.3 K of the suction check
and are excluded (``_EXCLUDED``).

Points that fail today are listed in ``KNOWN_FAILURES`` with their #96 defect
ID and run as ``xfail(strict=True)``; a fix removes exactly its entries.
"""

from __future__ import annotations

import itertools
import warnings

import pytest

from ._checks import KnownDefectObserved, check_steady, expect
from ._families import FAMILIES, SteadyCase

REFRIGERANTS = ("R32", "R290", "R410A", "R134a")
PART_LOAD = (0.25, 0.75)

# family -> (source temperatures [°C], demand temperatures [°C]); see _families for the meaning per family.
GRID: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "ASHPB": ((-7, 2, 7), (50, 55)),  # outdoor air / tank water
    "GSHPB": ((5, 10, 20), (40, 50)),  # source fluid / tank water
    "WSHPB": ((0, 10, 20), (50, 55)),  # source water / tank water
    "ASHP-heat": ((2, 7, 12), (18, 20)),  # outdoor air / room air
    "ASHP-cool": ((22, 26, 30), (35, 38)),  # room air / outdoor air
    "GSHP-heat": ((0, 5, 15), (18, 24)),  # borehole wall / room air
    "GSHP-cool": ((22, 26, 30), (15, 25)),  # room air / borehole wall
}

# Within 0.3 K of the suction <= source check on the reference platform.
_EXCLUDED = {("GSHPB", "R290", 5, 50, 0.25), ("WSHPB", "R410A", 10, 55, 0.25)}

_ALL_GSHP_POINTS = {
    "GSHP-heat": [(s, d, p) for s in (0, 5, 15) for d in (18, 24) for p in PART_LOAD],
    "GSHP-cool": [(s, d, p) for s in (22, 26, 30) for d in (15, 25) for p in PART_LOAD],
}
# What each grid defect is allowed to produce. A known-failing point that shows any
# *other* violation (or a different exception) fails hard instead of hiding behind
# its xfail mark.
_EXPECTED_TAGS: dict[str, set[str]] = {
    "C4": {"failed_with_heat"},
    "C11": {"suction_vs_source"},
}
# Defects that show up as a constructor failure: (exception type, text that identifies it).
_EXPECTED_CTOR_ERROR: dict[str, tuple[type[Exception], str]] = {}
_WHY = {
    "C4": "GSHP returns heat and power with converged=False / failure_reason='hx_not_converged'",
    "C11": "no superheat cap in GSHPB/WSHPB: suction refrigerant hotter than the source water",
}

# (family, refrigerant) -> {defect ID: [(T_source, T_demand, part_load), ...]} failing on 7fbd20c.
KNOWN_FAILURES: dict[tuple[str, str], dict[str, list[tuple[float, float, float]]]] = {
    ("WSHPB", "R134a"): {"C11": [(s, d, 0.25) for s in (0, 10, 20) for d in (50, 55)]},
    ("WSHPB", "R290"): {"C11": [(s, d, 0.25) for s in (0, 10, 20) for d in (50, 55)]},
    ("WSHPB", "R32"): {"C11": [(0, 50, 0.25), (0, 55, 0.25)]},
    ("WSHPB", "R410A"): {"C11": [(0, 50, 0.25), (0, 55, 0.25)]},
    ("GSHP-heat", "R32"): {"C4": _ALL_GSHP_POINTS["GSHP-heat"]},
    ("GSHP-heat", "R410A"): {"C4": _ALL_GSHP_POINTS["GSHP-heat"]},
    ("GSHP-heat", "R134a"): {"C4": [(s, d, 0.75) for s in (0, 5, 15) for d in (18, 24)]},
    ("GSHP-heat", "R290"): {"C4": [(s, d, 0.75) for s in (0, 5, 15) for d in (18, 24)]},
    ("GSHP-cool", "R32"): {"C4": _ALL_GSHP_POINTS["GSHP-cool"]},
    ("GSHP-cool", "R410A"): {"C4": _ALL_GSHP_POINTS["GSHP-cool"]},
    ("GSHP-cool", "R134a"): {"C4": [(s, d, 0.75) for s in (22, 26, 30) for d in (15, 25)]},
    ("GSHP-cool", "R290"): {"C4": [(s, d, 0.75) for s in (22, 26, 30) for d in (15, 25)]},
}


def _known_ids(family: str, ref: str, point: tuple[float, float, float]) -> list[str]:
    known = KNOWN_FAILURES.get((family, ref), {})
    return sorted(defect_id for defect_id, points in known.items() if point in points)


def _grid():
    for family, (sources, demands) in GRID.items():
        for ref, T_source, T_demand, plr in itertools.product(REFRIGERANTS, sources, demands, PART_LOAD):
            if (family, ref, T_source, T_demand, plr) in _EXCLUDED:
                continue
            # One xdist group per model prototype: each worker builds (or fails to build) it once.
            marks = [pytest.mark.xdist_group(f"invariants-{family}-{ref}")]
            ids = _known_ids(family, ref, (T_source, T_demand, plr))
            if ids:
                reason = "; ".join(f"#96 {i}: {_WHY[i]}" for i in ids)
                # Only KnownDefectObserved is absorbed; any other failure stays a failure.
                marks.append(pytest.mark.xfail(strict=True, raises=KnownDefectObserved, reason=reason))
            yield pytest.param(
                family,
                ref,
                float(T_source),
                float(T_demand),
                plr,
                id=f"{family}-{ref}-src{T_source}-dem{T_demand}-plr{plr}",
                marks=marks,
            )


def test_known_failures_reference_grid_points():
    """Every KNOWN_FAILURES entry names a point that is actually on the grid."""
    for (family, ref), by_id in KNOWN_FAILURES.items():
        sources, demands = GRID[family]
        assert ref in REFRIGERANTS
        for defect_id, points in by_id.items():
            assert defect_id in _WHY
            assert (defect_id in _EXPECTED_TAGS) != (defect_id in _EXPECTED_CTOR_ERROR), defect_id
            for s, d, p in points:
                assert s in sources and d in demands and p in PART_LOAD, (family, ref, s, d, p)
                assert (family, ref, s, d, p) not in _EXCLUDED


@pytest.mark.parametrize(("family", "ref", "T_source", "T_demand", "plr"), list(_grid()))
def test_steady_invariants(family, ref, T_source, T_demand, plr):
    fam = FAMILIES[family]
    known = _known_ids(family, ref, (T_source, T_demand, plr))
    try:
        model = fam.build(ref)
    except Exception as exc:
        for defect_id in known:
            error_type, text = _EXPECTED_CTOR_ERROR.get(defect_id, (type(None), ""))
            if isinstance(exc, error_type) and text in str(exc):
                raise KnownDefectObserved(f"#96 {defect_id}: {exc}") from exc
        raise
    rated = float(model.hp_capacity)
    case = SteadyCase(T_source=T_source, T_demand=T_demand, load_W=plr * rated)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = fam.call(model, case)
    violations = check_steady(fam, result, case, rated)
    allowed = set().union(*(_EXPECTED_TAGS.get(i, set()) for i in known))
    unexpected = [v for v in violations if v.split(":", 1)[0] not in allowed]
    if known and unexpected:
        # pytest.fail is not KnownDefectObserved, so the xfail mark cannot absorb it.
        pytest.fail("violations outside the known #96 defect:\n" + "\n".join(unexpected))
    if known:
        expect(violations == [], "\n".join(violations))
    assert violations == [], "\n".join(violations)
