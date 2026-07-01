# Docs interactive UX implementation plan

> **Status (2026-05-19): partially shipped.** Tasks 1, 4–11, 13, 14
> (foundation, P–h chart, validation table, composition tabs, glossary,
> Cmd+K, reading progress, scroll-spy) all landed on `main`. Tasks
> reverted after the live look-through: ② parity widget (Task 7),
> ③ 24h scrub (Task 8), ⑦ hero motion (Task 12), and the anchor-copy
> half of Task 14. The static SVG / rst fallbacks each reverted pattern
> sits on top of are now the page's only rendering. See the design
> spec's "Shipped state" header for the full table.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land nine isolated, individually-rollback-able interactive UX patterns on top of the existing Sphinx docs without rewriting any prose.

**Architecture:** D3 v7 cherry-picked + self-hosted. Vanilla JS for non-plot widgets. sphinx-design for tabs. Browser data is generated at build time by Python scripts that invoke CoolProp and tmhp itself — no runtime WASM. Each pattern mounts onto a fresh DOM node beside the existing static SVG/rst, so JS-disabled readers fall back to today's docs. One pattern = 1–3 commits = independent revert.

**Tech Stack:** Sphinx 7+ · Shibuya theme · D3 v7 (~50 KB cherry-pick, self-hosted) · Python 3.10+ · CoolProp · tmhp · vanilla JS · sphinx-design.

**Spec deviations from `2026-05-18-docs-interactive-ux-design.md`:**
- Build-time scripts live at `scripts/data/` (not `docs/source/_scripts/`) to follow the existing `scripts/validation/` and `scripts/visualization/` convention and to reuse `CATALOGUE` from `scripts/validation/samsung_ehs_parity.py`.
- `gen_timeseries_data.py` runs a real ASHPB dynamic simulation via the tmhp library to produce realistic data.
- `cycle_grid` in the per-refrigerant JSON ships only `{t_evap_c, t_cond_c, cop}` — the originally-planned `m_dot_kgs` and `q_cond_kw` arrays were dropped in commit `f8bcdfa` after code review showed they were anchored to a placeholder 0.5 kg/s and produced ~180 kW per cell. ṁ and Q_cond are heat-pump-system outputs, not refrigerant-cycle properties; they belong on the model pages, not the P–h widget. Task 6 (`ph-chart.js`) accordingly shows only COP in the side panel. The plan code blocks below for Task 6 still reference the dropped keys; the actual implementation reflects this deviation.

---

## File structure

```
scripts/data/                                 # NEW — build-time data generators
├── __init__.py
├── _common.py                                # shared helpers (paths, JSON writer)
├── gen_refrigerant_data.py                   # CoolProp → P–h dome + cycle grid (①)
├── gen_validation_data.py                    # CATALOGUE → 15-pt validation JSON (②④)
├── gen_timeseries_data.py                    # ASHPB 24h dynamic sim → JSON (③)
└── gen_glossary.py                           # hand-curated term list → JSON (⑥)

tests/data/                                   # NEW — pytest for generators
├── __init__.py
├── test_gen_refrigerant_data.py
├── test_gen_validation_data.py
├── test_gen_timeseries_data.py
└── test_gen_glossary.py

docs/source/_static/data/                     # NEW — generated JSON (committed)
├── glossary.json
├── refrigerants/{R32,R290,R134a,R1234yf}.json
├── validation-points.json
└── timeseries-24h.json

docs/source/_static/js/                       # NEW — frontend
├── lib/
│   ├── d3.v7.custom.min.js                   # cherry-pick: scale, shape, axis,
│   │                                          # selection, transition, array, color
│   └── cytoscape.min.js                      # moved from CDN
├── core/
│   ├── global.js                             # entry — imports/wires below
│   ├── reading-progress.js                   # ⑨
│   ├── scroll-spy.js                         # ⑨
│   ├── anchor-copy.js                        # ⑨
│   ├── glossary.js                           # ⑥
│   └── cmdk.js                               # ⑧
├── plots/
│   ├── _plot-common.js                       # shared helpers (Radix tokens, axis)
│   ├── ph-chart.js                           # ①
│   ├── parity-plot.js                        # ②
│   └── timeseries-scrub.js                   # ③
└── widgets/
    ├── validation-table.js                   # ④
    └── hero-motion.js                        # ⑦

docs/source/_static/css/                      # MODIFY — append global layer
└── custom.css                                # rules for: glossary, cmdk modal,
                                              # progress bar, hero motion, tabs,
                                              # plot containers

docs/source/_templates/                       # MODIFY
└── page.html                                 # add <script src=".../global.js" defer>

docs/Makefile                                 # MODIFY
                                              # add "data" target run before html
```

## Task index

| # | Phase | Task | Files added/touched | Commit |
|---|-------|------|---------------------|--------|
| 1 | 0 | Build-time data scripts + JSONs | `scripts/data/*`, `tests/data/*`, `_static/data/*` | `feat(docs/data): add CoolProp-driven build-time data scripts` |
| 2 | 0 | Self-host D3 cherry-pick | `_static/js/lib/d3.v7.custom.min.js` | `feat(docs/js): self-host cherry-picked D3 v7 bundle` |
| 3 | 0 | Self-host cytoscape | `_static/js/lib/cytoscape.min.js`, `concepts/cycle-architecture.rst` | `refactor(docs/js): self-host cytoscape for cycle-architecture` |
| 4 | 0 | Global JS entry point | `_templates/page.html`, `_static/js/core/global.js`, `custom.css` | `feat(docs/templates): add global JS entry point + CSS hooks` |
| 5 | 0 | Wire scripts into Makefile | `docs/Makefile`, `docs/source/conf.py` (excludes) | `build(docs): wire build-time data scripts into Makefile` |
| 6 | 1 | ① Live P–h chart | `plots/ph-chart.js`, `_plot-common.js`, 3 rst mounts, css | `feat(docs/concepts): live P–h chart with refrigerant selector` |
| 7 | 1 | ② Interactive parity plot | `plots/parity-plot.js`, validation/index.rst, css | `feat(docs/validation): interactive parity plot with hover cards` |
| 8 | 1 | ③ 24h timeseries scrub | `plots/timeseries-scrub.js`, 2 rst mounts, css | `feat(docs/tutorials): scrubable 24h timeseries` |
| 9 | 1 | ④ Filterable validation table | `widgets/validation-table.js`, validation/index.rst, css | `feat(docs/validation): filterable validation table` |
| 10 | 1 | ⑤ Composition tabs | 5 `models/*.rst`, css | `feat(docs/models): composition variant tabs via sphinx-design` |
| 11 | 2 | ⑥ Glossary popovers | `core/glossary.js`, glossary.json, concepts/models prose wraps, css | `feat(docs/core): inline glossary popovers` |
| 12 | 2 | ⑦ Hero motion + counter | `widgets/hero-motion.js`, index.rst, css | `feat(docs/landing): hero motion + metric counters` |
| 13 | 2 | ⑧ Cmd+K palette | `core/cmdk.js`, css | `feat(docs/core): command palette (Cmd+K)` |
| 14 | 2 | ⑨ Progress + scroll-spy + anchor copy | `core/reading-progress.js`, `scroll-spy.js`, `anchor-copy.js`, css | `feat(docs/core): reading progress, scroll-spy, anchor copy` |

---

## Phase 0 — Foundation

### Task 1: Build-time data generation scripts

**Files:**
- Create: `scripts/data/__init__.py`
- Create: `scripts/data/_common.py`
- Create: `scripts/data/gen_refrigerant_data.py`
- Create: `scripts/data/gen_validation_data.py`
- Create: `scripts/data/gen_timeseries_data.py`
- Create: `scripts/data/gen_glossary.py`
- Create: `tests/data/__init__.py`
- Create: `tests/data/test_gen_refrigerant_data.py`
- Create: `tests/data/test_gen_validation_data.py`
- Create: `tests/data/test_gen_timeseries_data.py`
- Create: `tests/data/test_gen_glossary.py`
- Generated: `docs/source/_static/data/**/*.json` (committed)

- [ ] **Step 1: Create `scripts/data/__init__.py`** (empty marker file)

```python
"""Build-time JSON data generators consumed by the interactive docs layer."""
```

- [ ] **Step 2: Create shared helper `scripts/data/_common.py`**

```python
"""Helpers shared by the build-time data generators.

These scripts run once per ``make html`` (wired in docs/Makefile) and
write JSON files into ``docs/source/_static/data/`` for the frontend to
consume. We keep this module small — paths, atomic write — and let the
specific generators own their domain logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Repo root = three levels above this file (scripts/data/_common.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "docs" / "source" / "_static" / "data"


def write_json(relative_path: str, payload: Any) -> Path:
    """Write ``payload`` as JSON under ``DATA_DIR`` and return the full path.

    Atomic via os.replace so a half-written file never lands in-tree.
    """
    target = DATA_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target
```

- [ ] **Step 3: Write failing tests for `gen_glossary.py`**

`tests/data/test_gen_glossary.py`:

```python
"""Tests for scripts/data/gen_glossary.py."""

from __future__ import annotations

import json

from scripts.data.gen_glossary import build_glossary


REQUIRED_TERMS = (
    "epsilon-ntu", "cop", "exv",
    "ashpb", "gshpb", "wshpb", "ashp", "gshp",
    "m-dot", "dt-evap",
    "eta-is", "eta-vol", "eta-mech",
)


def test_build_glossary_has_required_terms():
    glossary = build_glossary()
    missing = [t for t in REQUIRED_TERMS if t not in glossary]
    assert not missing, f"glossary missing terms: {missing}"


def test_glossary_entry_shape():
    glossary = build_glossary()
    for key, entry in glossary.items():
        assert isinstance(entry, dict), key
        assert {"name", "def", "link"} <= entry.keys(), (key, entry.keys())
        assert entry["name"] and entry["def"] and entry["link"], (key, entry)


def test_glossary_links_are_relative():
    glossary = build_glossary()
    for key, entry in glossary.items():
        link = entry["link"]
        assert not link.startswith(("http://", "https://")), (key, link)
        # Must be relative to docs/source — i.e. start with concepts/, models/, ...
        assert "/" in link, (key, link)


def test_glossary_writes_json(tmp_path, monkeypatch):
    """End-to-end: invoke the script's main() and verify it writes valid JSON."""
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_glossary import main
    main()
    out = tmp_path / "glossary.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "cop" in payload
```

- [ ] **Step 4: Verify tests fail with import error**

Run: `uv run pytest tests/data/test_gen_glossary.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.data.gen_glossary'` (4 failures).

- [ ] **Step 5: Implement `gen_glossary.py`**

```python
"""Build docs/source/_static/data/glossary.json from a curated term list."""

from __future__ import annotations

from scripts.data._common import write_json


TERMS: dict[str, dict[str, str]] = {
    "epsilon-ntu": {
        "name": "ε-NTU method",
        "def": "Heat-exchanger effectiveness expressed as a function of NTU "
               "and heat-capacity ratio C_r. Used for evaporator and "
               "condenser heat transfer.",
        "link": "concepts/cycle-architecture.html#the-shared-core",
    },
    "cop": {
        "name": "COP (coefficient of performance)",
        "def": "Useful heat output divided by electrical input. tmhp "
               "distinguishes cycle COP from system COP (includes pumps/fans).",
        "link": "concepts/why-physics-based.html",
    },
    "exv": {
        "name": "EXV (electronic expansion valve)",
        "def": "The expansion device that drops refrigerant pressure between "
               "condenser and evaporator. Modeled as isenthalpic in tmhp.",
        "link": "concepts/cycle-architecture.html",
    },
    "ashpb": {
        "name": "ASHPB (air-source heat pump boiler)",
        "def": "Air-source heat pump charging a DHW tank. Source side: "
               "outdoor coil with prescribed air state.",
        "link": "models/ashpb.html",
    },
    "gshpb": {
        "name": "GSHPB (ground-source heat pump boiler)",
        "def": "Ground-source heat pump charging a DHW tank. Source side: "
               "borehole field via g-function.",
        "link": "models/gshpb.html",
    },
    "wshpb": {
        "name": "WSHPB (water-source heat pump boiler)",
        "def": "Water-source heat pump charging a DHW tank. Source side: "
               "prescribed water inlet temperature.",
        "link": "models/wshpb.html",
    },
    "ashp": {
        "name": "ASHP (air-source heat pump, space conditioning)",
        "def": "Air-source heat pump driving a building load. "
               "Same core cycle as ASHPB; sink swaps to a building model.",
        "link": "models/ashp.html",
    },
    "gshp": {
        "name": "GSHP (ground-source heat pump, space conditioning)",
        "def": "Ground-source heat pump driving a building load. "
               "Same core cycle as GSHPB; sink swaps to a building model.",
        "link": "models/gshp.html",
    },
    "m-dot": {
        "name": "ṁ (mass flow rate)",
        "def": "Refrigerant mass flow. Set by compressor displacement, "
               "volumetric efficiency, rotational speed, and suction density.",
        "link": "concepts/cycle-architecture.html",
    },
    "dt-evap": {
        "name": "ΔT_evap (evaporator approach)",
        "def": "Difference between source-side fluid and evaporating "
               "refrigerant. tmhp solves it as a free parameter that "
               "minimizes compressor power.",
        "link": "concepts/cycle-architecture.html#the-shared-core",
    },
    "eta-is": {
        "name": "η_is (isentropic efficiency)",
        "def": "Ratio of ideal (isentropic) compression work to actual work.",
        "link": "models/ashpb.html",
    },
    "eta-vol": {
        "name": "η_vol (volumetric efficiency)",
        "def": "Fraction of compressor displacement volume actually filled "
               "with refrigerant on each stroke.",
        "link": "models/ashpb.html",
    },
    "eta-mech": {
        "name": "η_mech (mechanical efficiency)",
        "def": "Shaft-to-compression work ratio. Captures bearing and "
               "transmission losses.",
        "link": "models/ashpb.html",
    },
}


def build_glossary() -> dict[str, dict[str, str]]:
    """Return the canonical glossary mapping (term-id → entry)."""
    return TERMS


def main() -> None:
    write_json("glossary.json", build_glossary())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify glossary tests pass**

Run: `uv run pytest tests/data/test_gen_glossary.py -v`
Expected: 4 passed.

- [ ] **Step 7: Write failing tests for `gen_refrigerant_data.py`**

`tests/data/test_gen_refrigerant_data.py`:

```python
"""Tests for scripts/data/gen_refrigerant_data.py."""

from __future__ import annotations

import json
import pytest

from scripts.data.gen_refrigerant_data import (
    REFRIGERANTS,
    build_refrigerant_payload,
)


def test_supported_refrigerants():
    assert REFRIGERANTS == ("R32", "R290", "R134a", "R1234yf")


@pytest.mark.parametrize("ref", ["R32", "R290", "R134a", "R1234yf"])
def test_payload_top_level_shape(ref):
    payload = build_refrigerant_payload(ref)
    assert payload["refrigerant"] == ref
    assert {"saturation_dome", "isotherms", "cycle_grid"} <= payload.keys()


def test_saturation_dome_is_monotonic_in_pressure():
    payload = build_refrigerant_payload("R32")
    dome = payload["saturation_dome"]
    pressures_kpa = [pt["P_kpa"] for pt in dome]
    assert pressures_kpa == sorted(pressures_kpa), "dome must be sorted by P"


def test_saturation_dome_h_liquid_lt_h_vapor():
    payload = build_refrigerant_payload("R32")
    for pt in payload["saturation_dome"]:
        assert pt["h_liq_kjkg"] < pt["h_vap_kjkg"], pt


def test_cycle_grid_shape():
    payload = build_refrigerant_payload("R32")
    grid = payload["cycle_grid"]
    assert "t_evap_c" in grid and "t_cond_c" in grid
    assert "cop" in grid and "m_dot_kgs" in grid
    assert "q_cond_kw" in grid
    n_evap = len(grid["t_evap_c"])
    n_cond = len(grid["t_cond_c"])
    assert len(grid["cop"]) == n_evap
    assert len(grid["cop"][0]) == n_cond


def test_writes_per_refrigerant_files(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_refrigerant_data import main
    main()
    for ref in REFRIGERANTS:
        out = tmp_path / "refrigerants" / f"{ref}.json"
        assert out.exists(), out
        payload = json.loads(out.read_text())
        assert payload["refrigerant"] == ref
```

- [ ] **Step 8: Run tests, confirm they fail**

Run: `uv run pytest tests/data/test_gen_refrigerant_data.py -v`
Expected: import error / 6 failures.

- [ ] **Step 9: Implement `gen_refrigerant_data.py`**

```python
"""Pre-compute per-refrigerant thermodynamic data for the docs P–h chart.

Output (per refrigerant) is written to
``docs/source/_static/data/refrigerants/<REF>.json`` with three sections:

* ``saturation_dome``  ~80 points along (T_red → P, h_liq, h_vap)
* ``isotherms``        a handful of constant-T lines spanning [-30, +75 °C]
* ``cycle_grid``       a 21×21 grid of (T_evap, T_cond) → COP, m_dot, Q_cond

The frontend renders the dome + cycle points by bilinear-interpolating on
the grid, so we don't need runtime CoolProp.
"""

from __future__ import annotations

import numpy as np
from CoolProp.CoolProp import PropsSI

from scripts.data._common import write_json


REFRIGERANTS: tuple[str, ...] = ("R32", "R290", "R134a", "R1234yf")

# Cycle grid axes.
T_EVAP_RANGE_C = (-20.0, 20.0)
T_COND_RANGE_C = (25.0, 65.0)
GRID_N = 21  # 21×21 ≈ 441 points × 4 refrigerants ≈ ~50 KB per file

# Fixed parameters used to derive the cycle solution at each grid point.
SUPERHEAT_K = 5.0
SUBCOOL_K = 3.0
ETA_ISEN = 0.70


def saturation_dome(refrigerant: str, n_points: int = 80) -> list[dict[str, float]]:
    """Return ~80 dome points sampled in reduced-temperature space."""
    t_min = PropsSI("T_triple", refrigerant) + 1.0
    t_crit = PropsSI("T_critical", refrigerant)
    # Sample tighter near the critical point where the curve bends sharply.
    fractions = np.concatenate([
        np.linspace(0.05, 0.85, int(n_points * 0.7)),
        np.linspace(0.85, 0.995, n_points - int(n_points * 0.7)),
    ])
    out: list[dict[str, float]] = []
    for f in fractions:
        T = t_min + f * (t_crit - t_min)
        try:
            P = PropsSI("P", "T", T, "Q", 0, refrigerant)
            h_liq = PropsSI("H", "T", T, "Q", 0, refrigerant) / 1000.0
            h_vap = PropsSI("H", "T", T, "Q", 1, refrigerant) / 1000.0
        except ValueError:
            continue
        out.append({
            "T_c": T - 273.15,
            "P_kpa": P / 1000.0,
            "h_liq_kjkg": h_liq,
            "h_vap_kjkg": h_vap,
        })
    out.sort(key=lambda d: d["P_kpa"])
    return out


def isotherm(refrigerant: str, T_c: float, n_points: int = 40) -> list[dict[str, float]]:
    """Return one isotherm line (P, h) sweeping h across the dome and beyond."""
    T = T_c + 273.15
    # Pressure range: from ~0.1× saturation to ~5× saturation if subcritical.
    t_crit = PropsSI("T_critical", refrigerant)
    if T < t_crit:
        P_sat = PropsSI("P", "T", T, "Q", 0, refrigerant)
        P_range = np.geomspace(P_sat * 0.1, P_sat * 5.0, n_points)
    else:
        P_range = np.geomspace(1e5, 1e7, n_points)
    out: list[dict[str, float]] = []
    for P in P_range:
        try:
            h = PropsSI("H", "T", T, "P", P, refrigerant) / 1000.0
        except ValueError:
            continue
        out.append({"P_kpa": P / 1000.0, "h_kjkg": h})
    return out


def cycle_at(refrigerant: str, t_evap_c: float, t_cond_c: float) -> dict[str, float]:
    """Solve a simple isenthalpic-throttle cycle at one (T_evap, T_cond)."""
    T_evap = t_evap_c + 273.15
    T_cond = t_cond_c + 273.15
    T_suction = T_evap + SUPERHEAT_K
    T_subcooled = T_cond - SUBCOOL_K

    P_evap = PropsSI("P", "T", T_evap, "Q", 1, refrigerant)
    P_cond = PropsSI("P", "T", T_cond, "Q", 1, refrigerant)

    h_1 = PropsSI("H", "T", T_suction, "P", P_evap, refrigerant)       # comp in
    s_1 = PropsSI("S", "T", T_suction, "P", P_evap, refrigerant)
    h_2s = PropsSI("H", "S", s_1, "P", P_cond, refrigerant)            # ideal
    h_2 = h_1 + (h_2s - h_1) / ETA_ISEN                                # comp out
    h_3 = PropsSI("H", "T", T_subcooled, "P", P_cond, refrigerant)     # cond out
    h_4 = h_3                                                           # throttle

    w_comp = h_2 - h_1
    q_cond = h_2 - h_3
    cop = q_cond / w_comp if w_comp > 0 else float("nan")

    # Assume 0.5 kg/s reference; downstream caller can scale by Q_cond target.
    m_dot = 0.5

    return {
        "h_1": h_1 / 1000.0, "h_2": h_2 / 1000.0,
        "h_3": h_3 / 1000.0, "h_4": h_4 / 1000.0,
        "P_evap_kpa": P_evap / 1000.0,
        "P_cond_kpa": P_cond / 1000.0,
        "cop": cop,
        "m_dot_kgs": m_dot,
        "q_cond_kw": m_dot * (q_cond / 1000.0),
    }


def cycle_grid(refrigerant: str) -> dict:
    t_evap = np.linspace(*T_EVAP_RANGE_C, GRID_N).tolist()
    t_cond = np.linspace(*T_COND_RANGE_C, GRID_N).tolist()
    cop = []
    m_dot = []
    q_cond = []
    for te in t_evap:
        row_cop, row_m, row_q = [], [], []
        for tc in t_cond:
            try:
                c = cycle_at(refrigerant, te, tc)
            except ValueError:
                row_cop.append(None); row_m.append(None); row_q.append(None)
                continue
            row_cop.append(c["cop"])
            row_m.append(c["m_dot_kgs"])
            row_q.append(c["q_cond_kw"])
        cop.append(row_cop); m_dot.append(row_m); q_cond.append(row_q)
    return {
        "t_evap_c": t_evap,
        "t_cond_c": t_cond,
        "cop": cop,
        "m_dot_kgs": m_dot,
        "q_cond_kw": q_cond,
    }


def build_refrigerant_payload(refrigerant: str) -> dict:
    return {
        "refrigerant": refrigerant,
        "superheat_k": SUPERHEAT_K,
        "subcool_k": SUBCOOL_K,
        "eta_isen": ETA_ISEN,
        "saturation_dome": saturation_dome(refrigerant),
        "isotherms": [
            {"T_c": T, "points": isotherm(refrigerant, T)}
            for T in (-20.0, 0.0, 20.0, 40.0, 60.0)
        ],
        "cycle_grid": cycle_grid(refrigerant),
    }


def main() -> None:
    for ref in REFRIGERANTS:
        write_json(f"refrigerants/{ref}.json", build_refrigerant_payload(ref))


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Run refrigerant tests**

Run: `uv run pytest tests/data/test_gen_refrigerant_data.py -v`
Expected: 6 passed.

- [ ] **Step 11: Write failing tests for `gen_validation_data.py`**

`tests/data/test_gen_validation_data.py`:

```python
"""Tests for scripts/data/gen_validation_data.py."""

from __future__ import annotations

import json

from scripts.data.gen_validation_data import build_validation_points


def test_15_points():
    points = build_validation_points()
    assert len(points) == 15


def test_point_schema():
    points = build_validation_points()
    for p in points:
        assert {"case_id", "refrigerant", "t_source_c", "t_sink_c",
                "q_cat_kw", "q_mod_kw", "cop_cat", "cop_mod"} <= p.keys()
        assert isinstance(p["case_id"], int)
        assert p["q_cat_kw"] > 0
        assert p["q_mod_kw"] > 0


def test_writes_validation_json(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_validation_data import main
    main()
    out = tmp_path / "validation-points.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert len(payload) == 15
```

- [ ] **Step 12: Implement `gen_validation_data.py`**

```python
"""Reuse the CATALOGUE constant from samsung_ehs_parity and emit
docs/source/_static/data/validation-points.json with the same 15 points,
each evaluated by ASHPB so the docs widget reads off pre-computed numbers
instead of re-running the simulation in the browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Reuse the CATALOGUE from the existing validation script.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
from samsung_ehs_parity import CATALOGUE  # noqa: E402

from tmhp import AirSourceHeatPumpBoiler  # noqa: E402

from scripts.data._common import write_json


def build_validation_points() -> list[dict]:
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    out: list[dict] = []
    for op in CATALOGUE:
        result = ashpb.analyze_steady(
            T_tank_w=op.t_tank_c, T0=op.t0_c, Q_ref_cond=op.q_cond_kw * 1000.0,
        )
        out.append({
            "case_id": op.id,
            "refrigerant": "R32",
            "t_source_c": op.t0_c,
            "t_sink_c": op.lwt_c,
            "t_tank_c": op.t_tank_c,
            "q_cat_kw": op.q_cond_kw,
            "q_mod_kw": result["Q_ref_cond [W]"] / 1000.0,
            "cop_cat": op.target_cop,
            "cop_mod": result["cop_sys [-]"],
            "failure_reason": result.get("failure_reason", "none"),
        })
    return out


def main() -> None:
    write_json("validation-points.json", build_validation_points())


if __name__ == "__main__":
    main()
```

- [ ] **Step 13: Run validation tests**

Run: `uv run pytest tests/data/test_gen_validation_data.py -v`
Expected: 3 passed.

- [ ] **Step 14: Write tests for `gen_timeseries_data.py`**

`tests/data/test_gen_timeseries_data.py`:

```python
"""Tests for scripts/data/gen_timeseries_data.py."""

from __future__ import annotations

import json

from scripts.data.gen_timeseries_data import build_timeseries


def test_144_points_for_24h_at_10min():
    payload = build_timeseries()
    assert len(payload["series"]) == 144


def test_series_row_schema():
    payload = build_timeseries()
    for row in payload["series"]:
        assert {"t_min", "t_amb_c", "q_heat_kw", "cop", "p_cmp_kw"} <= row.keys()


def test_t_min_is_monotonic():
    payload = build_timeseries()
    times = [r["t_min"] for r in payload["series"]]
    assert times == sorted(times)
    assert times[0] == 0 and times[-1] == 23 * 60 + 50


def test_writes_timeseries_json(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_timeseries_data import main
    main()
    out = tmp_path / "timeseries-24h.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert len(payload["series"]) == 144
```

- [ ] **Step 15: Implement `gen_timeseries_data.py`**

The 24h simulation uses a sinusoidal ambient air profile, a residential
DHW draw schedule (morning + evening peaks), and tmhp's ASHPB. Output is
flat row-wise JSON for easy D3 ingestion.

```python
"""Run a representative 24-hour ASHPB dynamic simulation and emit JSON
for the interactive timeseries widget. The profile is a residential DHW
day: sinusoidal ambient, with morning (07:00) and evening (19:00) draws.
"""

from __future__ import annotations

import math

import numpy as np

from tmhp import AirSourceHeatPumpBoiler

from scripts.data._common import write_json


STEP_MIN = 10
N_STEPS = (24 * 60) // STEP_MIN  # 144

# Ambient: sinusoid centred on 10 °C, ±6 K daily swing, peak at 14:00.
AMBIENT_MEAN_C = 10.0
AMBIENT_AMP_C = 6.0
AMBIENT_PEAK_HOUR = 14.0

# DHW draw schedule: bursts at 07:00 (60 L) and 19:00 (80 L).
DHW_DRAWS = [(7.0, 60.0), (19.0, 80.0)]


def ambient_at(hour: float) -> float:
    phase = 2 * math.pi * (hour - (AMBIENT_PEAK_HOUR - 6.0)) / 24.0
    return AMBIENT_MEAN_C + AMBIENT_AMP_C * math.sin(phase)


def dhw_demand_kw_at(hour: float) -> float:
    """Crude pulse model: each draw spans ~10 min at 8 kW peak."""
    for h_peak, _vol in DHW_DRAWS:
        if abs(hour - h_peak) < 1 / 6:  # ±10 min window
            return 8.0
    return 0.0


def build_timeseries() -> dict:
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    series = []
    T_tank = 50.0
    for k in range(N_STEPS):
        t_min = k * STEP_MIN
        hour = t_min / 60.0
        t_amb = ambient_at(hour)
        q_demand_kw = dhw_demand_kw_at(hour)

        # Heat-pump charges whenever tank drops below target. Simple thermostat.
        q_call_kw = max(8.0 if T_tank < 53.0 else 0.0, q_demand_kw)
        cop, p_cmp_kw, q_cond_kw = float("nan"), 0.0, 0.0
        if q_call_kw > 0:
            res = ashpb.analyze_steady(
                T_tank_w=T_tank, T0=t_amb, Q_ref_cond=q_call_kw * 1000.0,
            )
            cop = res["cop_sys [-]"]
            p_cmp_kw = res["E_cmp [W]"] / 1000.0
            q_cond_kw = res["Q_ref_cond [W]"] / 1000.0

        # Naive tank update: 200 L mass, c_p 4.186 kJ/kg-K → ~837 kJ/K.
        net_kw = q_cond_kw - q_demand_kw
        T_tank += net_kw * STEP_MIN * 60.0 / 837.0

        series.append({
            "t_min": t_min,
            "t_amb_c": round(t_amb, 2),
            "q_heat_kw": round(q_cond_kw, 3),
            "q_demand_kw": round(q_demand_kw, 3),
            "p_cmp_kw": round(p_cmp_kw, 3),
            "cop": None if math.isnan(cop) else round(cop, 3),
            "t_tank_c": round(T_tank, 2),
        })

    return {
        "step_min": STEP_MIN,
        "refrigerant": "R32",
        "series": series,
    }


def main() -> None:
    write_json("timeseries-24h.json", build_timeseries())


if __name__ == "__main__":
    main()
```

- [ ] **Step 16: Run timeseries tests**

Run: `uv run pytest tests/data/test_gen_timeseries_data.py -v`
Expected: 4 passed.

- [ ] **Step 17: Run all data tests together + run the scripts end-to-end**

Run:
```
uv run pytest tests/data -v
uv run python -m scripts.data.gen_glossary
uv run python -m scripts.data.gen_refrigerant_data
uv run python -m scripts.data.gen_validation_data
uv run python -m scripts.data.gen_timeseries_data
```
Expected: 17 tests pass; four JSON files (and the `refrigerants/` directory with 4 files) appear under `docs/source/_static/data/`.

- [ ] **Step 18: Sanity-check JSON file sizes**

Run: `ls -la docs/source/_static/data/ docs/source/_static/data/refrigerants/`
Expected sizes (approximate):
- `glossary.json`: ~3 KB
- `validation-points.json`: ~4 KB
- `timeseries-24h.json`: ~15 KB
- `refrigerants/R32.json`: ~70 KB (and similar for others)

If any file is suspiciously empty (<500 B), inspect with `jq . docs/source/_static/data/<file>` and confirm it has populated arrays.

- [ ] **Step 19: Commit**

```bash
git add scripts/data tests/data docs/source/_static/data
git commit -m "feat(docs/data): add CoolProp-driven build-time data scripts

Four generators emit JSON consumed by the upcoming interactive docs
layer: refrigerant P–h dome + cycle grid (R32, R290, R134a, R1234yf),
the 15-point Samsung EHS catalogue parity data (reusing CATALOGUE from
scripts/validation), a 24-hour ASHPB dynamic timeseries, and the
glossary term list. Output lands under docs/source/_static/data/ and
is checked in; the Makefile will re-run the generators in a later
commit. 17 unit tests cover schema, monotonicity, and end-to-end
file writes."
```

---

### Task 2: Self-host cherry-picked D3 v7 bundle

**Files:**
- Create: `docs/source/_static/js/lib/d3.v7.custom.min.js`
- Create: `docs/source/_static/js/lib/D3_BUILD.md` (provenance note)

- [ ] **Step 1: Identify D3 modules we will use**

Cherry-pick set (matches the modules referenced in later plot tasks):
- `d3-array` (extent, max, bisector)
- `d3-axis` (axisBottom, axisLeft)
- `d3-color`
- `d3-format`
- `d3-interpolate`
- `d3-scale` (scaleLinear, scaleLog, scaleOrdinal)
- `d3-selection`
- `d3-shape` (line, area, curveCatmullRom)
- `d3-transition`

- [ ] **Step 2: Build a custom bundle with rollup**

Run (one-shot, no project dependency):

```bash
mkdir -p /tmp/d3-custom && cd /tmp/d3-custom
npm init -y
npm install --save-dev rollup @rollup/plugin-node-resolve @rollup/plugin-terser
npm install d3-array@3 d3-axis@3 d3-color@3 d3-format@3 \
            d3-interpolate@3 d3-scale@4 d3-selection@3 d3-shape@3 \
            d3-transition@3
```

Create `/tmp/d3-custom/entry.js`:

```javascript
export * from "d3-array";
export * from "d3-axis";
export * from "d3-color";
export * from "d3-format";
export * from "d3-interpolate";
export * from "d3-scale";
export * from "d3-selection";
export * from "d3-shape";
export * from "d3-transition";
```

Create `/tmp/d3-custom/rollup.config.mjs`:

```javascript
import resolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";

export default {
  input: "entry.js",
  output: { file: "d3.v7.custom.min.js", format: "iife", name: "d3" },
  plugins: [resolve(), terser()],
};
```

Run: `npx rollup -c`
Expected: `d3.v7.custom.min.js` produced (~45–55 KB).

- [ ] **Step 3: Copy the bundle into the repo**

Run:
```bash
cp /tmp/d3-custom/d3.v7.custom.min.js \
   /Users/wonjun/Codes/tmhp/docs/source/_static/js/lib/d3.v7.custom.min.js
```

- [ ] **Step 4: Record provenance in `D3_BUILD.md`**

```markdown
# D3 v7 custom bundle

`d3.v7.custom.min.js` is a self-hosted cherry-pick of D3 v7 covering only
the modules used by the interactive docs layer. It exposes one global
`d3` symbol.

## Modules

- d3-array, d3-axis, d3-color, d3-format, d3-interpolate
- d3-scale, d3-selection, d3-shape, d3-transition

## Rebuilding

The build is documented in
`docs/superpowers/plans/2026-05-18-docs-interactive-ux.md` (Task 2,
Step 2). Re-run that recipe to regenerate when bumping D3.

## Why self-hosted

CDN-hosted JS adds an external network dependency that is not present
elsewhere in the published docs. See the design doc, §A.3.
```

- [ ] **Step 5: Smoke-test the bundle**

Create `/tmp/d3-smoke.html`:

```html
<!doctype html><html><body>
<svg id="s" width="200" height="100"></svg>
<script src="/Users/wonjun/Codes/tmhp/docs/source/_static/js/lib/d3.v7.custom.min.js"></script>
<script>
  const s = d3.select("#s").append("circle")
    .attr("cx", 100).attr("cy", 50).attr("r", 30).attr("fill", "#3e63dd");
  console.log("d3 loaded:", typeof d3.scaleLinear, typeof d3.line);
</script>
</body></html>
```

Open in a browser. Open DevTools → Console.
Expected: a blue circle renders; console prints `d3 loaded: function function`.

- [ ] **Step 6: Commit**

```bash
git add docs/source/_static/js/lib/d3.v7.custom.min.js \
        docs/source/_static/js/lib/D3_BUILD.md
git commit -m "feat(docs/js): self-host cherry-picked D3 v7 bundle

~50 KB IIFE that exposes a single global \`d3\` covering the modules used
by the upcoming plot widgets: scale, shape, axis, selection, transition,
array, color, format, interpolate. Provenance + rebuild recipe in
\`_static/js/lib/D3_BUILD.md\`."
```

---

### Task 3: Self-host cytoscape

**Files:**
- Create: `docs/source/_static/js/lib/cytoscape.min.js`
- Modify: `docs/source/concepts/cycle-architecture.rst:99` (script src)

- [ ] **Step 1: Download cytoscape**

Run:
```bash
curl -L -o docs/source/_static/js/lib/cytoscape.min.js \
  https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js
ls -la docs/source/_static/js/lib/cytoscape.min.js
```
Expected: file ~320 KB.

- [ ] **Step 2: Pin the version with a SHA256 in a comment**

Run:
```bash
shasum -a 256 docs/source/_static/js/lib/cytoscape.min.js
```

Prepend a `// cytoscape v3.30.0 — sha256: <hash>` line to the file (do not modify the rest of the bundle).

- [ ] **Step 3: Update the script tag in `cycle-architecture.rst`**

In `docs/source/concepts/cycle-architecture.rst`, replace line 99:

```rst
   <script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js"></script>
```

with:

```rst
   <script src="../_static/js/lib/cytoscape.min.js"></script>
```

- [ ] **Step 4: Rebuild and visually confirm**

Run:
```bash
cd docs && uv run make clean && uv run make html
```
Open `docs/build/html/concepts/cycle-architecture.html` in a browser.
Verify the interactive cycle graph still renders (drag nodes, click a node to see code mapping).
Open DevTools → Network panel → refresh: no requests to `cdn.jsdelivr.net`.

- [ ] **Step 5: Commit**

```bash
git add docs/source/_static/js/lib/cytoscape.min.js \
        docs/source/concepts/cycle-architecture.rst
git commit -m "refactor(docs/js): self-host cytoscape for cycle-architecture

Removes the external cdn.jsdelivr.net dependency that the interactive
cycle graph in concepts/cycle-architecture.rst pulled at every page
load. The file (~320 KB) is now committed to _static/js/lib/ and pinned
by its sha256, matching the self-host policy used by the upcoming D3
layer."
```

---

### Task 4: Global JS entry point + base CSS hooks

**Files:**
- Create: `docs/source/_static/js/core/global.js`
- Modify: `docs/source/_templates/page.html`
- Modify: `docs/source/_static/css/custom.css` (append a hook section)

- [ ] **Step 1: Create the entry stub**

`docs/source/_static/js/core/global.js`:

```javascript
/**
 * docs interactive layer — global entry.
 *
 * Imports of individual modules are appended by each pattern's commit
 * (⑥ glossary, ⑧ cmdk, ⑨ progress / scroll-spy / anchor copy). Reverting
 * one of those commits removes both its module file and its import line
 * here, leaving this file cleanly smaller.
 *
 * This script is loaded with `defer`, so the DOM is parsed before it runs.
 */
(function () {
  "use strict";
  // Pattern hooks register themselves below.
  // (intentionally empty — populated by Phase 2 commits)
})();
```

- [ ] **Step 2: Modify `_templates/page.html` to load it**

Current file (`docs/source/_templates/page.html`):

```jinja
{%- extends "!page.html" -%}

{%- block content -%}
{{ super() }}
{%- if last_updated %}
<div class="git-last-updated" role="contentinfo">
  Last updated {{ last_updated }}
</div>
{%- endif %}
{%- endblock -%}
```

Append (at the end of the file):

```jinja
{%- block extrahead -%}
{{ super() }}
<script src="{{ pathto('_static/js/core/global.js', 1) }}" defer></script>
{%- endblock -%}
```

- [ ] **Step 3: Append the CSS hook section to `custom.css`**

Append at the end of `docs/source/_static/css/custom.css`:

```css
/* ===========================================================================
 * Interactive layer hooks (filled in by Phase 1 + Phase 2 commits).
 *
 * Sections that ship empty here are *placeholders for landmark comments*,
 * not for unfinished code. Each pattern's commit adds its own rules under
 * the matching section header below, so all interactive-layer CSS stays
 * grouped at the bottom of the file and the existing Radix-DNA layer above
 * remains untouched.
 * ------------------------------------------------------------------------- */

/* --- Plot containers (① ② ③) --- */
.tmhp-plot-mount {
    margin: 1.5em 0;
    font-family: var(--rx-font-sans);
}
.tmhp-plot-mount svg { display: block; max-width: 100%; height: auto; }
.tmhp-plot-mount .axis text { font-size: 12px; fill: var(--rx-ink-muted); }
.tmhp-plot-mount .axis line,
.tmhp-plot-mount .axis path { stroke: var(--rx-hairline); }

/* --- Table widget (④) --- */
/* --- Tabs (⑤) --- */
/* --- Glossary popover (⑥) --- */
/* --- Hero motion (⑦) --- */
/* --- Cmd+K palette (⑧) --- */
/* --- Reading progress + scroll-spy + anchor copy (⑨) --- */
```

- [ ] **Step 4: Rebuild and confirm the script is loaded**

Run:
```bash
cd docs && uv run make html
```
Open any built page (e.g. `docs/build/html/concepts/cycle-architecture.html`) in a browser.
DevTools → Network: `global.js` is requested with status 200.
Console: no errors.

- [ ] **Step 5: Confirm sphinx-build is warning-clean**

Run:
```bash
cd docs && uv run sphinx-build -W --keep-going -b html source build/html
```
Expected: exit code 0, no warnings printed. (Per the user's standing rule, the CI uses this exact flag; we must match.)

- [ ] **Step 6: Commit**

```bash
git add docs/source/_static/js/core/global.js \
        docs/source/_templates/page.html \
        docs/source/_static/css/custom.css
git commit -m "feat(docs/templates): add global JS entry point + CSS hooks

\`page.html\` now extends \`extrahead\` to inject a single deferred
\`<script src=\".../core/global.js\">\`. The script is a no-op IIFE; each
Phase 2 commit appends its import line, so reverting a Phase 2 commit
cleanly removes both the module and its registration.

Also adds the landmark CSS sections under custom.css's existing
Radix-DNA layer so per-pattern rules land grouped at the bottom of the
file rather than scattered."
```

---

### Task 5: Wire data scripts into Makefile

**Files:**
- Modify: `docs/Makefile`
- Modify: `docs/source/conf.py` (extend `exclude_patterns` if needed)

- [ ] **Step 1: Add a `data` target to the Makefile**

Edit `docs/Makefile`:

```make
# Minimal Makefile for Sphinx docs (cross-platform)
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build

.PHONY: help Makefile html clean data

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# Build-time JSON data consumed by the interactive UX layer.
# Re-run any time scripts/data/*.py changes or refrigerant set is bumped.
data:
	uv run python -m scripts.data.gen_glossary
	uv run python -m scripts.data.gen_refrigerant_data
	uv run python -m scripts.data.gen_validation_data
	uv run python -m scripts.data.gen_timeseries_data

html: data
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

clean:
	rm -rf $(BUILDDIR)/*

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
```

- [ ] **Step 2: Run a clean build and confirm both stages run**

Run:
```bash
cd docs && uv run make clean && uv run make html 2>&1 | tail -20
```
Expected: `gen_glossary`, `gen_refrigerant_data`, `gen_validation_data`, `gen_timeseries_data` all print no errors, then sphinx-build completes.

- [ ] **Step 3: Confirm data files are regenerated**

Run:
```bash
git status docs/source/_static/data
```
Expected: working tree clean (the regenerated JSON is identical to the committed one for deterministic inputs).

If non-deterministic drift surfaces (e.g. CoolProp floating-point variation across versions), document the cause in the script and consider rounding more aggressively before serialization. Do not commit the drifted file.

- [ ] **Step 4: Commit**

```bash
git add docs/Makefile
git commit -m "build(docs): wire build-time data scripts into Makefile

\`make html\` now runs the four scripts/data/gen_*.py generators before
sphinx-build, so JSON consumed by the interactive layer is always
in sync with the underlying tmhp / CoolProp versions. \`make data\`
is exposed as a standalone target for iterating on a generator
without rebuilding HTML."
```

---

## Phase 1 — Per-page interactions

### Task 6: ① Live P–h chart

**Files:**
- Create: `docs/source/_static/js/plots/_plot-common.js`
- Create: `docs/source/_static/js/plots/ph-chart.js`
- Modify: `docs/source/tutorials/visualize-the-cycle.rst`
- Modify: `docs/source/concepts/refrigerant-and-coolprop.rst`
- Modify: `docs/source/tutorials/swap-refrigerant.rst`
- Modify: `docs/source/_static/css/custom.css` (Plot containers section)

- [ ] **Step 1: Create the shared plot helper**

`docs/source/_static/js/plots/_plot-common.js`:

```javascript
/**
 * Shared helpers for tmhp interactive plots.
 *
 * Reads Radix-DNA CSS variables off :root so every plot picks up the
 * same ink, accent, hairline, and muted-text tokens the rest of the
 * page uses — keeping the visual language consistent across the docs.
 */
(function (root) {
  "use strict";

  function tokens() {
    const cs = getComputedStyle(document.documentElement);
    return {
      accent:    cs.getPropertyValue("--rx-accent-9").trim()  || "#3e63dd",
      accent11:  cs.getPropertyValue("--rx-accent-11").trim() || "#3a5bc7",
      accent3:   cs.getPropertyValue("--rx-accent-3").trim()  || "#edf2fe",
      ink:       cs.getPropertyValue("--rx-ink").trim()       || "#202020",
      muted:     cs.getPropertyValue("--rx-ink-muted").trim() || "#646464",
      hairline:  cs.getPropertyValue("--rx-hairline").trim()  || "rgba(0,0,0,0.15)",
      amber:     cs.getPropertyValue("--rx-amber-9").trim()   || "#ffb224",
      green:     cs.getPropertyValue("--rx-green-9").trim()   || "#30a46c",
      red:       cs.getPropertyValue("--rx-red-9").trim()     || "#e5484d",
    };
  }

  async function loadJson(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`fetch ${url} → ${r.status}`);
    return r.json();
  }

  /** Bilinear interpolation on a regular grid.
   *  @param {number[]} xs sorted ascending
   *  @param {number[]} ys sorted ascending
   *  @param {(number|null)[][]} z z[i][j] aligned to xs[i], ys[j]
   */
  function bilinear(xs, ys, z, x, y) {
    function bracket(arr, v) {
      if (v <= arr[0]) return [0, 0, 0];
      if (v >= arr[arr.length - 1]) return [arr.length - 1, arr.length - 1, 1];
      for (let i = 0; i < arr.length - 1; i++) {
        if (v >= arr[i] && v <= arr[i + 1]) {
          return [i, i + 1, (v - arr[i]) / (arr[i + 1] - arr[i])];
        }
      }
      return [arr.length - 1, arr.length - 1, 1];
    }
    const [i0, i1, tx] = bracket(xs, x);
    const [j0, j1, ty] = bracket(ys, y);
    const z00 = z[i0][j0], z01 = z[i0][j1], z10 = z[i1][j0], z11 = z[i1][j1];
    if ([z00, z01, z10, z11].some(v => v === null)) return null;
    return (z00 * (1 - tx) * (1 - ty)
          + z10 * tx       * (1 - ty)
          + z01 * (1 - tx) * ty
          + z11 * tx       * ty);
  }

  function staticDir() {
    // Resolve _static path relative to current page so widgets work at
    // any depth of the docs (e.g. tutorials/ vs concepts/ vs root).
    const here = window.location.pathname.replace(/\/$/, "");
    const parts = here.split("/").filter(Boolean);
    // Drop the last segment (filename); each remaining dir gets a ../
    const up = parts.length ? "../".repeat(parts.length - 1) : "";
    return up + "_static";
  }

  root.tmhpPlot = { tokens, loadJson, bilinear, staticDir };
})(window);
```

- [ ] **Step 2: Write `plots/ph-chart.js`** (the chart widget)

```javascript
/**
 * ① Live P–h chart with refrigerant selector.
 *
 * Reads /docs/source/_static/data/refrigerants/<REF>.json (built by
 * scripts/data/gen_refrigerant_data.py) and renders:
 *   - saturation dome (closed area on a log-P / h axis)
 *   - one selected isotherm
 *   - the four cycle state points (1: comp in, 2: comp out, 3: cond out,
 *     4: throttle out) with connecting line segments
 * COP, m_dot, Q_cond at the chosen (T_evap, T_cond) are shown in a
 * side panel and refresh as the sliders move. Sliders read from the
 * pre-computed cycle_grid via bilinear interpolation — no runtime
 * CoolProp.
 */
(function () {
  "use strict";
  const mount = document.getElementById("ph-chart-mount");
  if (!mount) return;
  const { tokens, loadJson, bilinear, staticDir } = window.tmhpPlot;

  const REFS = (mount.dataset.refrigerants || "R32").split(",");
  const DEFAULT_REF = mount.dataset.default || REFS[0];

  // Lay out the chrome: refrigerant select, two sliders, SVG, side card.
  mount.classList.add("tmhp-plot-mount", "ph-chart");
  mount.innerHTML = `
    <div class="ph-chrome">
      <label>Refrigerant
        <select class="ph-ref">${REFS.map(r => `<option>${r}</option>`).join("")}</select>
      </label>
      <label>T_evap <output class="ph-t-evap-out"></output>
        <input type="range" class="ph-t-evap" min="-20" max="20" step="1">
      </label>
      <label>T_cond <output class="ph-t-cond-out"></output>
        <input type="range" class="ph-t-cond" min="25" max="65" step="1">
      </label>
    </div>
    <div class="ph-canvas-wrap">
      <svg class="ph-canvas" viewBox="0 0 720 420" preserveAspectRatio="xMidYMid meet"></svg>
      <aside class="ph-readout">
        <div class="metric"><span class="label">COP</span><span class="value" data-k="cop">—</span></div>
        <div class="metric"><span class="label">ṁ</span><span class="value" data-k="m_dot">— kg/s</span></div>
        <div class="metric"><span class="label">Q_cond</span><span class="value" data-k="q_cond">— kW</span></div>
      </aside>
    </div>
  `;
  const sel = mount.querySelector(".ph-ref");
  const sliderEvap = mount.querySelector(".ph-t-evap");
  const sliderCond = mount.querySelector(".ph-t-cond");
  const outEvap = mount.querySelector(".ph-t-evap-out");
  const outCond = mount.querySelector(".ph-t-cond-out");
  const svg = mount.querySelector("svg.ph-canvas");
  const readout = mount.querySelector(".ph-readout");
  sel.value = DEFAULT_REF;
  sliderEvap.value = -5;
  sliderCond.value = 45;

  let payload = null;

  async function load(ref) {
    payload = await loadJson(`${staticDir()}/data/refrigerants/${ref}.json`);
    render();
  }

  function render() {
    if (!payload) return;
    const t = tokens();
    const dome = payload.saturation_dome;
    const grid = payload.cycle_grid;

    const margin = { top: 20, right: 20, bottom: 50, left: 60 };
    const W = 720, H = 420;
    const innerW = W - margin.left - margin.right;
    const innerH = H - margin.top - margin.bottom;

    const allH = dome.flatMap(d => [d.h_liq_kjkg, d.h_vap_kjkg]);
    const xExtent = [Math.min(...allH) - 20, Math.max(...allH) + 50];
    const pMin = Math.max(50, Math.min(...dome.map(d => d.P_kpa)) * 0.3);
    const pMax = Math.max(...dome.map(d => d.P_kpa)) * 1.2;

    const x  = d3.scaleLinear().domain(xExtent).range([0, innerW]);
    const yP = d3.scaleLog().domain([pMin, pMax]).range([innerH, 0]);

    svg.innerHTML = "";
    const root = d3.select(svg)
      .append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    // Axes
    root.append("g").attr("class", "axis")
        .attr("transform", `translate(0,${innerH})`)
        .call(d3.axisBottom(x).ticks(8).tickFormat(d3.format(",.0f")))
      .append("text").attr("x", innerW / 2).attr("y", 40)
        .attr("text-anchor", "middle").attr("fill", t.muted)
        .text("Enthalpy h [kJ/kg]");
    root.append("g").attr("class", "axis")
        .call(d3.axisLeft(yP).ticks(6, ".0f"))
      .append("text")
        .attr("transform", "rotate(-90)").attr("x", -innerH / 2).attr("y", -42)
        .attr("text-anchor", "middle").attr("fill", t.muted)
        .text("Pressure P [kPa, log]");

    // Saturation dome: liq side + vap side, joined.
    const domePath = d3.line()
      .x(d => x(d.h)).y(d => yP(d.P))
      .curve(d3.curveCatmullRom);
    const liq = dome.map(d => ({ h: d.h_liq_kjkg, P: d.P_kpa }));
    const vap = dome.map(d => ({ h: d.h_vap_kjkg, P: d.P_kpa })).reverse();
    root.append("path")
      .attr("d", domePath([...liq, ...vap]))
      .attr("fill", t.accent3).attr("fill-opacity", 0.5)
      .attr("stroke", t.accent11).attr("stroke-width", 1.2);

    // Cycle 4 points by interpolation at the chosen (T_evap, T_cond).
    const te = +sliderEvap.value, tc = +sliderCond.value;
    outEvap.textContent = `${te} °C`;
    outCond.textContent = `${tc} °C`;
    const xs = grid.t_evap_c, ys = grid.t_cond_c;

    // For state-point enthalpies, derive from grid by computing them at
    // the four nearest grid corners isn't quite right — instead we
    // re-derive from saturation + superheat/subcool deltas in the same
    // simple way the script did, but using the grid only for COP/m_dot.
    // The dome already gives us P_evap_sat(te) and P_cond_sat(tc).
    function P_sat(T_target, side /* "evap" | "cond" */) {
      // Linear in T_c along the dome.
      const tField = "T_c", pField = "P_kpa";
      const arr = dome;
      for (let i = 0; i < arr.length - 1; i++) {
        const a = arr[i], b = arr[i + 1];
        if (T_target >= a[tField] && T_target <= b[tField]) {
          const f = (T_target - a[tField]) / (b[tField] - a[tField]);
          return a[pField] + f * (b[pField] - a[pField]);
        }
      }
      return side === "evap" ? arr[0][pField] : arr[arr.length - 1][pField];
    }
    const P_evap = P_sat(te, "evap"), P_cond = P_sat(tc, "cond");
    // Cycle points: 1 = sat vapor at P_evap (approx, superheat omitted in viz);
    // 2 = at P_cond with extra Δh from cycle; 3 = sat liq at P_cond; 4 = h3 (throttle).
    function h_at(P, q) {
      // Look up dome at this P (nearest in pressure) — q=0 liq, q=1 vap.
      let best = dome[0], bestErr = Math.abs(dome[0].P_kpa - P);
      for (const d of dome) {
        const e = Math.abs(d.P_kpa - P);
        if (e < bestErr) { best = d; bestErr = e; }
      }
      return q < 0.5 ? best.h_liq_kjkg : best.h_vap_kjkg;
    }
    const h1 = h_at(P_evap, 1);
    const h3 = h_at(P_cond, 0);
    const h4 = h3;
    // For h2, take h1 + (q_cond - (h1 - h4)) / cop_estimate — quick visual hint.
    const cop = bilinear(xs, ys, grid.cop, te, tc);
    const q_cond = bilinear(xs, ys, grid.q_cond_kw, te, tc);
    const m_dot = bilinear(xs, ys, grid.m_dot_kgs, te, tc);
    // q_evap_per_kg = h1 - h4; q_cond_per_kg = q_evap_per_kg + w_per_kg
    // w_per_kg = q_cond_per_kg / cop  (approx)
    const q_evap_pkg = h1 - h4;
    const w_pkg = q_evap_pkg / Math.max(cop - 1, 0.5);
    const h2 = h1 + w_pkg;

    const cyclePoints = [
      { h: h1, P: P_evap, label: "1" },
      { h: h2, P: P_cond, label: "2" },
      { h: h3, P: P_cond, label: "3" },
      { h: h4, P: P_evap, label: "4" },
    ];
    const cycleClosed = [...cyclePoints, cyclePoints[0]];
    const cycleLine = d3.line().x(d => x(d.h)).y(d => yP(d.P));
    root.append("path")
      .attr("d", cycleLine(cycleClosed))
      .attr("fill", "none").attr("stroke", t.accent).attr("stroke-width", 1.8);

    root.selectAll(".pt").data(cyclePoints).enter().append("g")
      .attr("class", "pt")
      .attr("transform", d => `translate(${x(d.h)},${yP(d.P)})`)
      .call(g => {
        g.append("circle").attr("r", 4).attr("fill", t.accent);
        g.append("text").attr("x", 6).attr("y", -6).attr("fill", t.ink)
          .attr("font-size", 12).attr("font-weight", 600).text(d => d.label);
      });

    // Readout
    readout.querySelector('[data-k="cop"]').textContent =
      cop ? cop.toFixed(2) : "—";
    readout.querySelector('[data-k="m_dot"]').textContent =
      m_dot ? `${m_dot.toFixed(2)} kg/s` : "—";
    readout.querySelector('[data-k="q_cond"]').textContent =
      q_cond ? `${q_cond.toFixed(2)} kW` : "—";
  }

  sel.addEventListener("change", () => load(sel.value));
  sliderEvap.addEventListener("input", render);
  sliderCond.addEventListener("input", render);

  load(DEFAULT_REF);
})();
```

- [ ] **Step 3: Add CSS for the chart**

Append under `/* --- Plot containers (① ② ③) --- */` in `custom.css`:

```css
.ph-chart .ph-chrome {
    display: flex; gap: var(--rx-space-4);
    flex-wrap: wrap; align-items: center;
    padding: var(--rx-space-3); border: 1px solid var(--rx-hairline);
    border-radius: var(--rx-radius-3);
    background: var(--rx-gray-1);
}
.ph-chart .ph-chrome label {
    display: flex; gap: var(--rx-space-2); align-items: center;
    font-size: var(--rx-fs-2); color: var(--rx-ink-muted);
}
.ph-chart .ph-chrome select,
.ph-chart .ph-chrome input[type="range"] {
    accent-color: var(--rx-accent-9);
}
.ph-chart .ph-canvas-wrap {
    display: grid; grid-template-columns: 1fr 160px;
    gap: var(--rx-space-4); margin-top: var(--rx-space-3);
    align-items: start;
}
.ph-chart .ph-readout {
    display: flex; flex-direction: column; gap: var(--rx-space-2);
    padding: var(--rx-space-3);
    background: var(--rx-accent-3);
    border-radius: var(--rx-radius-3);
}
.ph-chart .ph-readout .metric {
    display: flex; justify-content: space-between; font-size: var(--rx-fs-2);
}
.ph-chart .ph-readout .metric .label { color: var(--rx-ink-muted); }
.ph-chart .ph-readout .metric .value {
    color: var(--rx-accent-11); font-weight: var(--rx-fw-medium);
    font-variant-numeric: tabular-nums;
}
@media (max-width: 720px) {
    .ph-chart .ph-canvas-wrap { grid-template-columns: 1fr; }
}
```

- [ ] **Step 4: Add the mount block to `tutorials/visualize-the-cycle.rst`**

Find a place in the file (after the static `mollier_cycle_R32.svg` figure) and insert:

```rst
.. raw:: html

   <div id="ph-chart-mount"
        data-refrigerants="R32,R290,R134a,R1234yf"
        data-default="R32"></div>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/plots/ph-chart.js"></script>
```

- [ ] **Step 5: Add the same mount to `concepts/refrigerant-and-coolprop.rst`**

Same `.. raw:: html` block as above. The mount id is page-scoped to a single chart per page; if the design ever needs multiple, change `getElementById` to a `querySelectorAll` loop (out of scope here).

- [ ] **Step 6: Add the mount to `tutorials/swap-refrigerant.rst`**

Same block again.

- [ ] **Step 7: Rebuild and verify**

Run:
```bash
cd docs && uv run make html
```

Open `docs/build/html/tutorials/visualize-the-cycle.html`. Expected:
- Refrigerant dropdown shows R32 / R290 / R134a / R1234yf.
- Two sliders work; readout updates live.
- Switching refrigerant redraws the dome.
- The existing static `mollier_cycle_R32.svg` figure remains visible above/below.

Open DevTools → Disable JavaScript → reload. Expected:
- The interactive widget is empty (the mount div has no content), the static SVG is still there.

- [ ] **Step 8: Confirm sphinx warning-clean**

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`
Expected: exit 0, no warnings.

- [ ] **Step 9: Commit**

```bash
git add docs/source/_static/js/plots/_plot-common.js \
        docs/source/_static/js/plots/ph-chart.js \
        docs/source/_static/css/custom.css \
        docs/source/tutorials/visualize-the-cycle.rst \
        docs/source/concepts/refrigerant-and-coolprop.rst \
        docs/source/tutorials/swap-refrigerant.rst
git commit -m "feat(docs/concepts): live P–h chart with refrigerant selector

Reads pre-computed JSON from _static/data/refrigerants/<REF>.json and
renders the saturation dome + the four cycle state points with D3.
Refrigerant select + (T_evap, T_cond) sliders rebind interactively;
COP / ṁ / Q_cond appear in a side card and update via bilinear
interpolation on the cycle grid. The existing static mollier SVG is
untouched and continues to serve as the JS-disabled fallback."
```

---

### Task 7: ② Interactive parity plot

**Files:**
- Create: `docs/source/_static/js/plots/parity-plot.js`
- Modify: `docs/source/validation/index.rst`
- Modify: `docs/source/_static/css/custom.css` (Plot containers section)

- [ ] **Step 1: Implement `parity-plot.js`**

```javascript
/**
 * ② Interactive parity plot (15-point Samsung EHS catalogue).
 *
 * Reads /_static/data/validation-points.json (built by
 * scripts/data/gen_validation_data.py). Renders a scatter of Q_mod vs
 * Q_cat with the y = x reference line. Hovering a point pins a side
 * card with the case fields; clicking a point dispatches a CustomEvent
 * 'tmhp:parity-selected' that ④ (validation-table) listens for to
 * highlight the matching row.
 */
(function () {
  "use strict";
  const mount = document.getElementById("parity-plot-mount");
  if (!mount) return;
  const { tokens, loadJson, staticDir } = window.tmhpPlot;

  mount.classList.add("tmhp-plot-mount", "parity-plot");
  mount.innerHTML = `
    <div class="parity-wrap">
      <svg class="parity-canvas" viewBox="0 0 600 460" preserveAspectRatio="xMidYMid meet"></svg>
      <aside class="parity-readout">
        <div class="empty">Hover or click a point to see the case</div>
      </aside>
    </div>
  `;
  const svg = mount.querySelector("svg.parity-canvas");
  const card = mount.querySelector(".parity-readout");

  (async () => {
    const points = await loadJson(`${staticDir()}/data/validation-points.json`);
    const t = tokens();
    const margin = { top: 30, right: 20, bottom: 50, left: 60 };
    const W = 600, H = 460;
    const iw = W - margin.left - margin.right;
    const ih = H - margin.top - margin.bottom;
    const qs = points.flatMap(p => [p.q_cat_kw, p.q_mod_kw]);
    const lo = Math.floor(Math.min(...qs) - 1);
    const hi = Math.ceil(Math.max(...qs) + 1);

    const x = d3.scaleLinear().domain([lo, hi]).range([0, iw]);
    const y = d3.scaleLinear().domain([lo, hi]).range([ih, 0]);

    const root = d3.select(svg).append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    root.append("g").attr("class", "axis")
      .attr("transform", `translate(0,${ih})`)
      .call(d3.axisBottom(x).ticks(6))
      .append("text").attr("x", iw / 2).attr("y", 40)
      .attr("text-anchor", "middle").attr("fill", t.muted)
      .text("Catalogue Q [kW]");
    root.append("g").attr("class", "axis")
      .call(d3.axisLeft(y).ticks(6))
      .append("text")
      .attr("transform", "rotate(-90)").attr("x", -ih / 2).attr("y", -42)
      .attr("text-anchor", "middle").attr("fill", t.muted)
      .text("Model Q [kW]");

    root.append("line")
      .attr("x1", x(lo)).attr("y1", y(lo))
      .attr("x2", x(hi)).attr("y2", y(hi))
      .attr("stroke", t.muted).attr("stroke-dasharray", "4 4");

    let selectedId = null;

    const dots = root.selectAll("circle.pt")
      .data(points).enter()
      .append("circle").attr("class", "pt")
      .attr("cx", d => x(d.q_cat_kw))
      .attr("cy", d => y(d.q_mod_kw))
      .attr("r", 5).attr("fill", t.accent)
      .attr("stroke", "#fff").attr("stroke-width", 1.2)
      .style("cursor", "pointer");

    function show(d) {
      const dpct = ((d.q_mod_kw - d.q_cat_kw) / d.q_cat_kw) * 100;
      const sign = dpct >= 0 ? "+" : "";
      const cls = Math.abs(dpct) < 5 ? "ok" : "warn";
      card.innerHTML = `
        <div class="head">Case ${d.case_id} · ${d.refrigerant}</div>
        <dl>
          <dt>T_source / T_sink</dt><dd>${d.t_source_c} / ${d.t_sink_c} °C</dd>
          <dt>Q_cat</dt><dd>${d.q_cat_kw.toFixed(2)} kW</dd>
          <dt>Q_mod</dt><dd>${d.q_mod_kw.toFixed(2)} kW</dd>
          <dt>COP_cat / COP_mod</dt><dd>${d.cop_cat.toFixed(2)} / ${d.cop_mod.toFixed(2)}</dd>
        </dl>
        <div class="delta ${cls}">${sign}${dpct.toFixed(1)} %</div>
      `;
    }

    dots
      .on("mouseenter", (_e, d) => show(d))
      .on("mouseleave", () => {
        if (selectedId !== null) {
          const sel = points.find(p => p.case_id === selectedId);
          if (sel) show(sel);
        } else {
          card.innerHTML = '<div class="empty">Hover or click a point to see the case</div>';
        }
      })
      .on("click", (_e, d) => {
        selectedId = d.case_id;
        dots.attr("fill", p => p.case_id === selectedId ? t.amber : t.accent);
        window.dispatchEvent(new CustomEvent("tmhp:parity-selected",
          { detail: { case_id: d.case_id } }));
      });

    // Listen for selection from ④ (table → plot).
    window.addEventListener("tmhp:table-selected", (e) => {
      selectedId = e.detail.case_id;
      dots.attr("fill", p => p.case_id === selectedId ? t.amber : t.accent);
      const sel = points.find(p => p.case_id === selectedId);
      if (sel) show(sel);
    });
  })();
})();
```

- [ ] **Step 2: Add CSS**

Append under `/* --- Plot containers (① ② ③) --- */`:

```css
.parity-plot .parity-wrap {
    display: grid; grid-template-columns: 1fr 220px;
    gap: var(--rx-space-4); align-items: start;
}
.parity-plot .parity-readout {
    padding: var(--rx-space-3);
    border: 1px solid var(--rx-hairline);
    border-radius: var(--rx-radius-3);
    background: var(--rx-gray-1);
    font-size: var(--rx-fs-2);
}
.parity-plot .parity-readout .empty { color: var(--rx-ink-muted); font-style: italic; }
.parity-plot .parity-readout .head { font-weight: var(--rx-fw-bold); color: var(--rx-accent-11); margin-bottom: var(--rx-space-2); }
.parity-plot .parity-readout dl { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; margin: 0; }
.parity-plot .parity-readout dt { color: var(--rx-ink-muted); }
.parity-plot .parity-readout dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.parity-plot .parity-readout .delta { margin-top: var(--rx-space-2); font-weight: var(--rx-fw-medium); font-size: var(--rx-fs-3); }
.parity-plot .parity-readout .delta.ok   { color: var(--rx-green-11); }
.parity-plot .parity-readout .delta.warn { color: var(--rx-amber-11); }
@media (max-width: 720px) {
    .parity-plot .parity-wrap { grid-template-columns: 1fr; }
}
```

- [ ] **Step 3: Add the mount to `validation/index.rst`**

Locate the `.. figure:: ../_static/validation_parity.svg` block. Insert *above* it:

```rst
.. raw:: html

   <div id="parity-plot-mount"></div>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/plots/parity-plot.js"></script>
```

Keep the figure block as-is — it remains the no-JS fallback.

- [ ] **Step 4: Build and verify**

Run: `cd docs && uv run make html`
Open `docs/build/html/validation/index.html`. Confirm:
- Scatter plot renders with 15 dots.
- y = x dashed line spans the data range.
- Hovering a dot fills the side card with case fields.
- Clicking a dot turns it amber and persists the card.
- The static `validation_parity.svg` figure still appears below.

- [ ] **Step 5: Sphinx warning check + commit**

```bash
cd docs && uv run sphinx-build -W --keep-going -b html source build/html
git add docs/source/_static/js/plots/parity-plot.js \
        docs/source/_static/css/custom.css \
        docs/source/validation/index.rst
git commit -m "feat(docs/validation): interactive parity plot with hover cards

Renders the 15-point Samsung EHS parity scatter from pre-computed
JSON. Hovering a point shows the case_id, source/sink temperatures,
catalogue vs model Q and COP, and a colour-coded percent error.
Clicking a point pins it and fires a 'tmhp:parity-selected' event
(consumed by the upcoming filterable validation table). The static
SVG figure below remains as the JS-disabled fallback."
```

---

### Task 8: ③ 24h timeseries scrub

**Files:**
- Create: `docs/source/_static/js/plots/timeseries-scrub.js`
- Modify: `docs/source/tutorials/realistic-dynamic-simulation.rst`
- Modify: `docs/source/getting-started/first-dynamic-simulation.rst`
- Modify: `docs/source/_static/css/custom.css`

- [ ] **Step 1: Implement `timeseries-scrub.js`**

The widget draws three stacked subplots sharing a time axis (T_amb / Q_heat / COP). A vertical cursor follows pointer X; a side card shows the row at that index. Double-click resets pan/zoom.

```javascript
/**
 * ③ 24-hour timeseries scrub.
 *
 * Reads /_static/data/timeseries-24h.json (built by
 * scripts/data/gen_timeseries_data.py). Three stacked subplots share
 * the time axis; a vertical cursor + side-card show the row at the
 * pointer position.
 */
(function () {
  "use strict";
  const mount = document.getElementById("ts-scrub-mount");
  if (!mount) return;
  const { tokens, loadJson, staticDir } = window.tmhpPlot;

  mount.classList.add("tmhp-plot-mount", "ts-scrub");
  mount.innerHTML = `
    <div class="ts-wrap">
      <svg class="ts-canvas" viewBox="0 0 780 460" preserveAspectRatio="xMidYMid meet"></svg>
      <aside class="ts-readout">
        <div class="head">Scrub to pin a time</div>
        <dl></dl>
      </aside>
    </div>
  `;
  const svg = mount.querySelector("svg.ts-canvas");
  const headEl = mount.querySelector(".ts-readout .head");
  const dlEl = mount.querySelector(".ts-readout dl");

  (async () => {
    const payload = await loadJson(`${staticDir()}/data/timeseries-24h.json`);
    const data = payload.series;
    const t = tokens();

    const W = 780, H = 460;
    const margin = { top: 20, right: 30, bottom: 40, left: 60 };
    const innerW = W - margin.left - margin.right;
    const innerH = H - margin.top - margin.bottom;
    const rowH = innerH / 3;

    const x = d3.scaleLinear()
      .domain([data[0].t_min, data[data.length - 1].t_min])
      .range([0, innerW]);

    const seriesDefs = [
      { key: "t_amb_c",  label: "T_amb [°C]",   color: t.amber },
      { key: "q_heat_kw",label: "Q_heat [kW]",  color: t.accent },
      { key: "cop",      label: "COP [-]",      color: t.green },
    ];

    const root = d3.select(svg).append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    seriesDefs.forEach((s, i) => {
      const yExt = d3.extent(data, d => d[s.key]).map(v => v ?? 0);
      // Pad y a bit so the line doesn't touch the subplot edges.
      const pad = (yExt[1] - yExt[0]) * 0.1 || 1;
      const y = d3.scaleLinear()
        .domain([yExt[0] - pad, yExt[1] + pad])
        .range([(i + 1) * rowH - 4, i * rowH + 4]);

      // y-axis (left) per row
      root.append("g").attr("class", "axis")
        .call(d3.axisLeft(y).ticks(3));
      root.append("text").attr("x", 4).attr("y", i * rowH + 14)
        .attr("fill", t.muted).attr("font-size", 11).text(s.label);

      const line = d3.line()
        .defined(d => d[s.key] !== null && d[s.key] !== undefined)
        .x(d => x(d.t_min)).y(d => y(d[s.key]));
      root.append("path").datum(data)
        .attr("d", line)
        .attr("fill", "none").attr("stroke", s.color).attr("stroke-width", 1.5);
    });

    // Shared x-axis at the bottom (hours).
    root.append("g").attr("class", "axis")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(x)
        .ticks(8)
        .tickFormat(m => `${String(Math.floor(m / 60)).padStart(2,"0")}:00`));

    // Cursor overlay
    const cursor = root.append("line")
      .attr("y1", 0).attr("y2", innerH)
      .attr("stroke", t.ink).attr("stroke-width", 1)
      .style("display", "none");

    const bisect = d3.bisector(d => d.t_min).left;

    function update(mouseX) {
      const tMin = x.invert(mouseX);
      const idx = Math.max(0, Math.min(data.length - 1, bisect(data, tMin)));
      const row = data[idx];
      cursor.attr("x1", x(row.t_min)).attr("x2", x(row.t_min)).style("display", null);
      const hh = String(Math.floor(row.t_min / 60)).padStart(2, "0");
      const mm = String(row.t_min % 60).padStart(2, "0");
      headEl.textContent = `${hh}:${mm}`;
      const rows = [
        ["T_amb",   row.t_amb_c   != null ? row.t_amb_c.toFixed(1)   + " °C" : "—"],
        ["Q_heat",  row.q_heat_kw != null ? row.q_heat_kw.toFixed(2) + " kW" : "—"],
        ["COP",     row.cop       != null ? row.cop.toFixed(2)               : "—"],
        ["P_cmp",   row.p_cmp_kw  != null ? row.p_cmp_kw.toFixed(2)  + " kW" : "—"],
        ["T_tank",  row.t_tank_c  != null ? row.t_tank_c.toFixed(1)  + " °C" : "—"],
      ];
      dlEl.innerHTML = rows.map(([k, v]) =>
        `<dt>${k}</dt><dd>${v}</dd>`).join("");
    }

    root.append("rect")
      .attr("width", innerW).attr("height", innerH)
      .attr("fill", "transparent")
      .on("mousemove", (event) => {
        const [mx] = d3.pointer(event, this);
        update(mx);
      });
  })();
})();
```

- [ ] **Step 2: Add CSS**

Append:

```css
.ts-scrub .ts-wrap {
    display: grid; grid-template-columns: 1fr 220px;
    gap: var(--rx-space-4); align-items: start;
}
.ts-scrub .ts-readout {
    padding: var(--rx-space-3);
    border: 1px solid var(--rx-hairline);
    border-radius: var(--rx-radius-3);
    background: var(--rx-gray-1);
    font-size: var(--rx-fs-2);
}
.ts-scrub .ts-readout .head { font-weight: var(--rx-fw-bold); color: var(--rx-accent-11); margin-bottom: var(--rx-space-2); font-variant-numeric: tabular-nums; }
.ts-scrub .ts-readout dl { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; margin: 0; }
.ts-scrub .ts-readout dt { color: var(--rx-ink-muted); }
.ts-scrub .ts-readout dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 720px) {
    .ts-scrub .ts-wrap { grid-template-columns: 1fr; }
}
```

- [ ] **Step 3: Mount in `tutorials/realistic-dynamic-simulation.rst`**

After the existing `dynamic_24h_timeseries.svg` figure, insert:

```rst
.. raw:: html

   <div id="ts-scrub-mount"></div>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/plots/timeseries-scrub.js"></script>
```

- [ ] **Step 4: Mount in `getting-started/first-dynamic-simulation.rst`**

Same block (path stays `../_static/...` — both pages are at depth 2).

- [ ] **Step 5: Build, smoke-test, sphinx warning check**

Run: `cd docs && uv run make html`
Verify the three-row plot renders. Move the mouse across — cursor and side card update.

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add docs/source/_static/js/plots/timeseries-scrub.js \
        docs/source/_static/css/custom.css \
        docs/source/tutorials/realistic-dynamic-simulation.rst \
        docs/source/getting-started/first-dynamic-simulation.rst
git commit -m "feat(docs/tutorials): scrubable 24h timeseries

Three stacked subplots (T_amb / Q_heat / COP) sharing a time axis,
with a vertical cursor that follows the pointer and a side card that
shows the row at the cursor's timestamp. Powered by pre-computed
24-hour ASHPB simulation JSON. The static dynamic_24h_timeseries.svg
figure above remains as the JS-disabled fallback."
```

---

### Task 9: ④ Filterable validation table

**Files:**
- Create: `docs/source/_static/js/widgets/validation-table.js`
- Modify: `docs/source/validation/index.rst`
- Modify: `docs/source/_static/css/custom.css` (Table widget section)

- [ ] **Step 1: Implement `validation-table.js`**

```javascript
/**
 * ④ Filterable, sortable validation table.
 *
 * Reads the same /_static/data/validation-points.json as the parity
 * plot. On successful hydration, the existing rst-rendered table is
 * hidden via CSS class (and remains in the DOM as the JS-disabled
 * fallback). Row click → 'tmhp:table-selected' event (consumed by ②).
 */
(function () {
  "use strict";
  const mount = document.getElementById("validation-table-mount");
  if (!mount) return;
  const { loadJson, staticDir } = window.tmhpPlot;

  // Hide the static rst table sibling once we're alive.
  const staticTable = document.querySelector(".validation-table-static");
  if (staticTable) staticTable.classList.add("hidden-by-js");

  mount.classList.add("validation-table");
  mount.innerHTML = `
    <div class="vt-chrome">
      <input class="vt-filter" placeholder="Filter (try '7 °C' or 'R32')…">
      <div class="vt-chips"></div>
    </div>
    <table class="vt-table">
      <thead><tr>
        <th data-sort="case_id">Case</th>
        <th data-sort="refrigerant">Ref.</th>
        <th data-sort="t_source_c">T_src [°C]</th>
        <th data-sort="t_sink_c">T_sink [°C]</th>
        <th data-sort="q_cat_kw">Q_cat [kW]</th>
        <th data-sort="q_mod_kw">Q_mod [kW]</th>
        <th data-sort="delta_pct">Δ [%]</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  `;
  const filterEl = mount.querySelector(".vt-filter");
  const chipsEl = mount.querySelector(".vt-chips");
  const tbody = mount.querySelector("tbody");
  let rows = [];
  let sortKey = "case_id";
  let sortAsc = true;
  let chipFilter = null;
  let selectedId = null;

  function deltaPct(r) {
    return ((r.q_mod_kw - r.q_cat_kw) / r.q_cat_kw) * 100;
  }

  function render() {
    const q = filterEl.value.trim().toLowerCase();
    let visible = rows.filter(r => {
      const blob = `${r.case_id} ${r.refrigerant} ${r.t_source_c} ${r.t_sink_c} ${r.q_cat_kw} ${r.q_mod_kw}`.toLowerCase();
      const hitText = !q || blob.includes(q);
      const hitChip = !chipFilter || r.refrigerant === chipFilter;
      return hitText && hitChip;
    });

    visible.sort((a, b) => {
      const av = sortKey === "delta_pct" ? deltaPct(a) : a[sortKey];
      const bv = sortKey === "delta_pct" ? deltaPct(b) : b[sortKey];
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

    tbody.innerHTML = visible.map(r => {
      const d = deltaPct(r);
      const cls = Math.abs(d) < 5 ? "ok" : "warn";
      const sel = r.case_id === selectedId ? " is-selected" : "";
      return `<tr data-case="${r.case_id}" class="vt-row${sel}">
        <td>${r.case_id}</td>
        <td>${r.refrigerant}</td>
        <td>${r.t_source_c}</td>
        <td>${r.t_sink_c}</td>
        <td>${r.q_cat_kw.toFixed(2)}</td>
        <td>${r.q_mod_kw.toFixed(2)}</td>
        <td class="${cls}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</td>
      </tr>`;
    }).join("");
  }

  (async () => {
    rows = await loadJson(`${staticDir()}/data/validation-points.json`);

    // Build refrigerant chips
    const refs = [...new Set(rows.map(r => r.refrigerant))];
    chipsEl.innerHTML = refs.map(r =>
      `<button class="vt-chip" data-ref="${r}">${r}</button>`).join("");
    chipsEl.addEventListener("click", e => {
      const b = e.target.closest(".vt-chip");
      if (!b) return;
      const r = b.dataset.ref;
      chipFilter = chipFilter === r ? null : r;
      chipsEl.querySelectorAll(".vt-chip").forEach(c =>
        c.classList.toggle("active", c.dataset.ref === chipFilter));
      render();
    });

    filterEl.addEventListener("input", render);

    mount.querySelectorAll("th[data-sort]").forEach(th => {
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (sortKey === k) sortAsc = !sortAsc;
        else { sortKey = k; sortAsc = true; }
        render();
      });
    });

    tbody.addEventListener("click", e => {
      const tr = e.target.closest("tr.vt-row");
      if (!tr) return;
      selectedId = +tr.dataset.case;
      render();
      window.dispatchEvent(new CustomEvent("tmhp:table-selected",
        { detail: { case_id: selectedId } }));
    });

    window.addEventListener("tmhp:parity-selected", e => {
      selectedId = e.detail.case_id;
      render();
      const tr = tbody.querySelector(`tr[data-case="${selectedId}"]`);
      if (tr) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });

    render();
  })();
})();
```

- [ ] **Step 2: Add CSS for table widget**

Under `/* --- Table widget (④) --- */`:

```css
.validation-table .vt-chrome {
    display: flex; gap: var(--rx-space-3); margin-bottom: var(--rx-space-3);
    flex-wrap: wrap; align-items: center;
}
.validation-table .vt-filter {
    flex: 1; min-width: 200px; padding: 6px 10px;
    border: 1px solid var(--rx-hairline); border-radius: var(--rx-radius-3);
    font-family: var(--rx-font-sans); font-size: var(--rx-fs-2);
    background: var(--rx-gray-1); color: var(--rx-ink);
}
.validation-table .vt-chip {
    padding: 4px 10px; border: 1px solid var(--rx-hairline);
    background: var(--rx-gray-1); border-radius: var(--rx-radius-full);
    font-size: var(--rx-fs-1); cursor: pointer;
    color: var(--rx-ink-muted);
}
.validation-table .vt-chip.active {
    background: var(--rx-accent-3); border-color: var(--rx-accent-6);
    color: var(--rx-accent-11); font-weight: var(--rx-fw-medium);
}
.validation-table .vt-table {
    width: 100%; border-collapse: collapse; font-size: var(--rx-fs-2);
}
.validation-table .vt-table th,
.validation-table .vt-table td {
    padding: 6px 8px; text-align: right;
    border-bottom: 1px solid var(--rx-hairline-soft);
    font-variant-numeric: tabular-nums;
}
.validation-table .vt-table th:first-child,
.validation-table .vt-table td:first-child,
.validation-table .vt-table th:nth-child(2),
.validation-table .vt-table td:nth-child(2) { text-align: left; }
.validation-table .vt-table th {
    cursor: pointer; user-select: none;
    color: var(--rx-ink-muted); font-weight: var(--rx-fw-medium);
}
.validation-table .vt-row.is-selected { background: var(--rx-accent-3); }
.validation-table .vt-table td.ok   { color: var(--rx-green-11); }
.validation-table .vt-table td.warn { color: var(--rx-amber-11); }
.hidden-by-js { display: none !important; }
```

- [ ] **Step 3: Update `validation/index.rst`**

Find the existing static table and wrap it (or the surrounding container) with the marker class. Above the static table, insert the mount:

```rst
.. raw:: html

   <div id="validation-table-mount"></div>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/widgets/validation-table.js"></script>
```

Then make sure the existing rst table is rendered inside a container that JS can find. Adjust the existing `.. list-table::` directive to use the `:class: validation-table-static` option, e.g.:

```rst
.. list-table::
   :class: validation-table-static
   :header-rows: 1
   ...
```

If the current page uses a different markup (markdown, raw HTML table, etc.), wrap it with a `.. container:: validation-table-static` block.

- [ ] **Step 4: Build + smoke + sphinx-W**

Run: `cd docs && uv run make html`
Open `docs/build/html/validation/index.html`. Confirm:
- Static rst table is hidden (the new widget shows instead).
- Typing into the filter narrows the rows.
- Refrigerant chip toggles in/out.
- Clicking a column header sorts.
- Clicking a row highlights the matching parity point above.
- Clicking a parity point selects the matching row and scrolls it into view.

JS disabled → static rst table reappears (no `hidden-by-js` class applied).

- [ ] **Step 5: Commit**

```bash
git add docs/source/_static/js/widgets/validation-table.js \
        docs/source/_static/css/custom.css \
        docs/source/validation/index.rst
git commit -m "feat(docs/validation): filterable validation table

Hydrates a sortable, filterable, refrigerant-chipped table over
_static/data/validation-points.json. Bidirectional link with the
interactive parity plot above via 'tmhp:parity-selected' /
'tmhp:table-selected' CustomEvents. The pre-existing rst list-table
is kept in source and hidden by CSS only when the JS widget
successfully hydrates, so JS-disabled readers still see a full table."
```

---

### Task 10: ⑤ Composition variant tabs (sphinx-design only)

**Files:**
- Modify: `docs/source/models/ashpb.rst`
- Modify: `docs/source/models/gshpb.rst`
- Modify: `docs/source/models/wshpb.rst`
- Modify: `docs/source/models/ashp.rst`
- Modify: `docs/source/models/gshp.rst`
- Modify: `docs/source/_static/css/custom.css` (Tabs section)

- [ ] **Step 1: Add the tab block to `models/ashpb.rst`**

Inspect the current file structure first. Find the section that currently describes composition variants (or replaces the existing "Composed subsystems" prose with the tab block). Add at the appropriate location:

```rst
.. tab-set::
   :class: composition-tabs

   .. tab-item:: Base

      The standalone ASHPB: outdoor coil ε-NTU evaporator, R32 cycle,
      DHW tank charge.

      .. code-block:: python

         from tmhp import AirSourceHeatPumpBoiler
         ashpb = AirSourceHeatPumpBoiler(ref="R32")
         result = ashpb.analyze_steady(T_tank_w=55.0, T0=5.0, Q_ref_cond=8_000.0)

   .. tab-item:: + STC preheat

      Adds a flat-plate STC that preheats mains water entering the
      tank. Reduces the tank-charge duty the heat pump has to deliver.

      .. code-block:: python

         from tmhp import ASHPB_STC_preheat
         model = ASHPB_STC_preheat(ref="R32", stc_area=4.0)

   .. tab-item:: + STC stratified

      STC charges a separate top node of a stratified tank; the heat
      pump charges the bottom. Top-of-tank water is drawn first.

      .. code-block:: python

         from tmhp import ASHPB_STC_tank
         model = ASHPB_STC_tank(ref="R32", stc_area=4.0, n_nodes=4)

   .. tab-item:: + PV / ESS

      Photovoltaic generation + ESS preferentially feeds the
      compressor and auxiliaries.

      .. code-block:: python

         from tmhp import ASHPB_PV_ESS
         model = ASHPB_PV_ESS(ref="R32", pv_kw=3.0, ess_kwh=5.0)
```

- [ ] **Step 2: Add equivalent tab blocks to the other four model pages**

Mirror the structure above for `gshpb.rst` (GSHPB_STC_preheat, GSHPB_STC_tank, GSHPB_PV_ESS), `wshpb.rst` (no STC variants — keep just "Base"; remove any tabs that don't have a matching tmhp class), `ashp.rst` (Base only or with subsystems that exist for ASHP), `gshp.rst` (Base only). Cross-check `src/tmhp/__init__.py` to confirm which subsystem combinations are actually exported.

For pages where only a Base variant exists, skip the tab-set entirely — don't show a single-tab UI.

- [ ] **Step 3: Add the CSS for the tabs**

Under `/* --- Tabs (⑤) --- */`:

```css
/* sphinx-design tabs reskin to match Radix-DNA accent. */
.composition-tabs .sd-tab-set { border-bottom: 1px solid var(--rx-hairline); }
.composition-tabs .sd-tab-label {
    padding: var(--rx-space-2) var(--rx-space-4);
    font-size: var(--rx-fs-2);
    color: var(--rx-ink-muted);
    border-bottom: 2px solid transparent;
    transition: color 120ms ease, border-color 120ms ease;
}
.composition-tabs .sd-tab-label:hover { color: var(--rx-ink); }
.composition-tabs input.sd-tab-input:checked + label.sd-tab-label {
    color: var(--rx-accent-11);
    border-bottom-color: var(--rx-accent-9);
    font-weight: var(--rx-fw-medium);
}
.composition-tabs .sd-tab-content { padding: var(--rx-space-4) 0; }
```

- [ ] **Step 4: Build, verify visually, sphinx-W**

Run: `cd docs && uv run make html`
Open each model page; click between tabs; confirm the accent underline + content swap. No-JS fallback (sphinx-design renders as accordion by default).

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`

- [ ] **Step 5: Commit**

```bash
git add docs/source/models docs/source/_static/css/custom.css
git commit -m "feat(docs/models): composition variant tabs via sphinx-design

Each model page (ashpb, gshpb, wshpb, ashp, gshp) now exposes its
composed-subsystem variants as a sphinx-design tab-set. Reskin
matches the Radix-DNA accent underline used elsewhere in the docs;
no client-side JS is added — sphinx-design's static fallback works
for no-JS readers."
```

---

## Phase 2 — Global layer

### Task 11: ⑥ Inline glossary popovers

**Files:**
- Create: `docs/source/_static/js/core/glossary.js`
- Modify: `docs/source/_static/js/core/global.js` (register hook)
- Modify: `docs/source/_static/css/custom.css` (Glossary popover section)
- Modify: prose in `docs/source/concepts/*.rst` and the heads of `docs/source/models/*.rst` to wrap canonical terms.

- [ ] **Step 1: Implement `core/glossary.js`**

```javascript
/**
 * ⑥ Inline glossary popovers.
 *
 * Wraps an existing `<span class="glossary" data-term="xxx">` in any
 * page with hover/focus → small popover showing the term name, a 1-2
 * line definition, and a link to the concept page. Terms come from
 * /_static/data/glossary.json.
 */
(function () {
  "use strict";

  const STATE = {
    terms: null,
    active: null,    // currently open span
    pop: null,       // floating popover element
  };

  function staticDir() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) + "_static" : "_static";
  }

  async function loadTerms() {
    if (STATE.terms) return STATE.terms;
    const r = await fetch(`${staticDir()}/data/glossary.json`, { credentials: "same-origin" });
    STATE.terms = await r.json();
    return STATE.terms;
  }

  function ensurePopover() {
    if (STATE.pop) return STATE.pop;
    const el = document.createElement("div");
    el.className = "glossary-pop";
    el.setAttribute("role", "tooltip");
    document.body.appendChild(el);
    STATE.pop = el;
    return el;
  }

  function show(span, entry, baseUrl) {
    const pop = ensurePopover();
    pop.innerHTML = `
      <div class="head">${entry.name}</div>
      <div class="def">${entry.def}</div>
      <a class="link" href="${baseUrl}/${entry.link}">↳ Concepts page</a>
    `;
    const r = span.getBoundingClientRect();
    pop.style.top = `${window.scrollY + r.bottom + 4}px`;
    pop.style.left = `${window.scrollX + r.left}px`;
    pop.classList.add("visible");
    STATE.active = span;
  }

  function hide() {
    if (STATE.pop) STATE.pop.classList.remove("visible");
    STATE.active = null;
  }

  async function attach() {
    const spans = document.querySelectorAll("span.glossary[data-term]");
    if (!spans.length) return;
    const terms = await loadTerms();
    const baseUrl = staticDir().replace(/\/_static$/, "");

    spans.forEach(span => {
      const term = terms[span.dataset.term];
      if (!term) return;
      span.tabIndex = 0;
      span.setAttribute("aria-label", term.name);

      span.addEventListener("mouseenter", () => show(span, term, baseUrl));
      span.addEventListener("mouseleave", (e) => {
        // Allow hover to move onto the popover without flicker.
        const next = e.relatedTarget;
        if (next && STATE.pop && STATE.pop.contains(next)) return;
        hide();
      });
      span.addEventListener("focus", () => show(span, term, baseUrl));
      span.addEventListener("blur", hide);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
    document.addEventListener("click", (e) => {
      if (STATE.active && !STATE.active.contains(e.target) &&
          STATE.pop && !STATE.pop.contains(e.target)) hide();
    });
  }

  window.tmhpGlossary = { attach };
})();
```

- [ ] **Step 2: Register from `global.js`**

Update `docs/source/_static/js/core/global.js`:

```javascript
(function () {
  "use strict";
  // ⑥ glossary
  if (window.tmhpGlossary && typeof window.tmhpGlossary.attach === "function") {
    document.addEventListener("DOMContentLoaded", window.tmhpGlossary.attach);
  }
})();
```

And ensure `_templates/page.html` loads `glossary.js` *before* `global.js`. Update the `extrahead` block:

```jinja
{%- block extrahead -%}
{{ super() }}
<script src="{{ pathto('_static/js/core/glossary.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/global.js', 1) }}" defer></script>
{%- endblock -%}
```

- [ ] **Step 3: CSS for popover**

Under `/* --- Glossary popover (⑥) --- */`:

```css
.glossary {
    border-bottom: 1px dashed var(--rx-accent-11);
    cursor: help;
    padding: 0 1px;
}
.glossary:hover, .glossary:focus { outline: none; background: var(--rx-accent-3); border-radius: 2px; }
.glossary-pop {
    position: absolute; z-index: 9999;
    max-width: 280px;
    padding: var(--rx-space-3);
    background: var(--rx-gray-1);
    color: var(--rx-ink);
    border: 1px solid var(--rx-accent-6);
    border-radius: var(--rx-radius-3);
    box-shadow: 0 8px 24px rgba(0,0,0,0.10);
    font-size: var(--rx-fs-2);
    opacity: 0; pointer-events: none;
    transition: opacity 100ms ease;
}
.glossary-pop.visible { opacity: 1; pointer-events: auto; }
.glossary-pop .head  { font-weight: var(--rx-fw-bold); color: var(--rx-accent-11); }
.glossary-pop .def   { margin-top: 4px; color: var(--rx-ink); }
.glossary-pop .link  { display: inline-block; margin-top: 6px; color: var(--rx-accent-11); font-size: var(--rx-fs-1); text-decoration: none; }
.glossary-pop .link:hover { text-decoration: underline; }
```

- [ ] **Step 4: Wrap canonical terms in concepts pages**

In `docs/source/concepts/cycle-architecture.rst`, locate the first occurrence of "ε-NTU" and wrap via the `raw:: html` substitution pattern or `:raw-html:` role. Simplest, file-local: define a substitution at the top of each affected rst file:

```rst
.. |epsilon-ntu| raw:: html

   <span class="glossary" data-term="epsilon-ntu">ε-NTU</span>

.. |cop| raw:: html

   <span class="glossary" data-term="cop">COP</span>

.. |exv| raw:: html

   <span class="glossary" data-term="exv">EXV</span>

.. (etc for: m-dot, dt-evap, ashpb, gshpb, wshpb, ashp, gshp, eta-is, eta-vol, eta-mech)
```

Then in prose, replace `ε-NTU` with `|epsilon-ntu|`, `COP` with `|cop|`, etc. Only do this for **first occurrence** in each file — repeated underlines would be noisy. Files to edit (limit scope):

- `concepts/cycle-architecture.rst` — ε-NTU, COP, EXV, m-dot, dt-evap
- `concepts/why-physics-based.rst` — COP, η-is, η-vol, η-mech
- `concepts/refrigerant-and-coolprop.rst` — COP
- `concepts/failure-reason-semantics.rst` — (no glossary terms — skip)
- `models/ashpb.rst` (first paragraph) — ASHPB
- `models/gshpb.rst` (first paragraph) — GSHPB
- `models/wshpb.rst` — WSHPB
- `models/ashp.rst` — ASHP
- `models/gshp.rst` — GSHP

- [ ] **Step 5: Build, verify**

Run: `cd docs && uv run make html`
Open `docs/build/html/concepts/cycle-architecture.html`. Confirm:
- ε-NTU, COP, EXV have a dotted underline.
- Hover over ε-NTU opens a popover with name + def + concept-page link.
- Esc closes; click outside closes.

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`

- [ ] **Step 6: Commit**

```bash
git add docs/source/_static/js/core/glossary.js \
        docs/source/_static/js/core/global.js \
        docs/source/_templates/page.html \
        docs/source/_static/css/custom.css \
        docs/source/concepts docs/source/models
git commit -m "feat(docs/core): inline glossary popovers

Adds a glossary core script that hydrates spans of the form
\`<span class=\"glossary\" data-term=\"…\">\` with hover/focus
popovers sourced from _static/data/glossary.json. Wraps the canonical
domain terms (ε-NTU, COP, EXV, ASHPB, etc.) at their first occurrence
in concepts/ and the heads of models/ pages. Dotted underline + light
accent tint = pit-of-success affordance; the popover is keyboard-
accessible and Esc-dismissible. JS-disabled readers see only the
underline."
```

---

### Task 12: ⑦ Hero motion + counter

**Files:**
- Create: `docs/source/_static/js/widgets/hero-motion.js`
- Modify: `docs/source/index.rst`
- Modify: `docs/source/_static/css/custom.css` (Hero motion section)

- [ ] **Step 1: Implement `widgets/hero-motion.js`**

```javascript
/**
 * ⑦ Landing hero motion: counters + P–h sketch fade-in.
 *
 * Runs only on the landing page (detects #hero-motion-root). Honours
 * prefers-reduced-motion by skipping the animation and rendering the
 * final state immediately.
 */
(function () {
  "use strict";
  const root = document.getElementById("hero-motion-root");
  if (!root) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const counters = root.querySelectorAll(".hero-metric");
  const sketch = root.querySelector(".hero-sketch");

  function animateCount(el) {
    const target = +el.dataset.target;
    if (reduceMotion || target === 0) {
      el.textContent = target;
      return;
    }
    const duration = 800;
    const start = performance.now();
    function tick(now) {
      const k = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(target * ease);
      if (k < 1) requestAnimationFrame(tick);
      else el.textContent = target;
    }
    requestAnimationFrame(tick);
  }

  function reveal() {
    if (sketch && !reduceMotion) sketch.classList.add("is-visible");
    counters.forEach(animateCount);
  }

  if ("IntersectionObserver" in window && !reduceMotion) {
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { reveal(); obs.disconnect(); break; }
      }
    }, { threshold: 0.2 });
    obs.observe(root);
  } else {
    reveal();
  }
})();
```

- [ ] **Step 2: Replace the hero block in `index.rst`**

Find the existing `.. container:: hero-badges` and `.. container:: hero-cta` blocks. Replace them (and the immediately preceding lead paragraph if needed) with a `.. raw:: html` block:

```rst
.. raw:: html

   <div id="hero-motion-root" class="hero">
     <div class="hero-stats">
       <div class="hero-stat"><span class="hero-metric" data-target="15">0</span>
         <span class="hero-stat-label">benchmark points</span></div>
       <div class="hero-stat"><span class="hero-metric" data-target="5">0</span>
         <span class="hero-stat-label">model families</span></div>
       <div class="hero-stat"><span class="hero-metric" data-target="0">0</span>
         <span class="hero-stat-label">fitted curves</span></div>
     </div>
     <svg class="hero-sketch" viewBox="0 0 300 140" aria-hidden="true">
       <path d="M 20 110 Q 30 70 70 55 Q 110 40 150 40 Q 190 40 220 55 Q 250 70 260 110"
             fill="#edf2fe" stroke="#3a5bc7" stroke-width="1"/>
       <path d="M 40 90 L 40 50 L 210 50 L 210 90 Z"
             fill="none" stroke="#3e63dd" stroke-width="2"/>
     </svg>
   </div>
   <script src="_static/js/widgets/hero-motion.js" defer></script>
```

(Keep the existing hero CTA / badge content above — the motion block is additive.)

- [ ] **Step 3: CSS for hero motion**

Under `/* --- Hero motion (⑦) --- */`:

```css
.hero { margin: var(--rx-space-6) 0; }
.hero-stats { display: flex; gap: var(--rx-space-6); flex-wrap: wrap; }
.hero-stat { display: flex; flex-direction: column; gap: 2px; }
.hero-stat .hero-metric {
    font-size: var(--rx-fs-7); font-weight: var(--rx-fw-bold);
    color: var(--rx-accent-11); font-variant-numeric: tabular-nums;
}
.hero-stat .hero-stat-label {
    font-size: var(--rx-fs-1); color: var(--rx-ink-muted);
    text-transform: uppercase; letter-spacing: 0.05em;
}
.hero-sketch {
    margin-top: var(--rx-space-4);
    max-width: 360px; height: auto;
    opacity: 0; transform: translateY(8px);
    transition: opacity 600ms ease, transform 600ms ease;
}
.hero-sketch.is-visible { opacity: 1; transform: translateY(0); }
@media (prefers-reduced-motion: reduce) {
    .hero-sketch { opacity: 1; transform: none; transition: none; }
}
```

- [ ] **Step 4: Build, smoke, sphinx-W**

Run: `cd docs && uv run make html`
Open `docs/build/html/index.html`. Confirm:
- Three metric counters animate from 0 to 15 / 5 / 0 on first scroll.
- P–h sketch fades in.
- macOS / browser "reduce motion" preference → numbers appear immediately, no fade.

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`

- [ ] **Step 5: Commit**

```bash
git add docs/source/_static/js/widgets/hero-motion.js \
        docs/source/index.rst \
        docs/source/_static/css/custom.css
git commit -m "feat(docs/landing): hero motion + metric counters

Adds three counters (15 / 5 / 0) that animate from 0 to target on
first IntersectionObserver hit, plus a small P–h sketch SVG that
fades in. Both are gated by prefers-reduced-motion. Decorative only;
no functional content is added or rewritten."
```

---

### Task 13: ⑧ Command palette (Cmd+K)

**Files:**
- Create: `docs/source/_static/js/core/cmdk.js`
- Modify: `docs/source/_static/js/core/global.js`
- Modify: `docs/source/_templates/page.html` (script tag)
- Modify: `docs/source/_static/css/custom.css` (Cmd+K palette section)

- [ ] **Step 1: Implement `core/cmdk.js`**

The palette reads Sphinx's `_static/searchindex.js` (which exposes a global `Search.setIndex` callback) to obtain document titles and the doc-name → URL mapping. We treat the index as a simple title list — full-text ranking is left to Sphinx's `/search/` page.

```javascript
/**
 * ⑧ Command palette (Cmd+K / Ctrl+K).
 *
 * Reads page titles + URLs from Sphinx's _static/searchindex.js. Opens
 * a modal with a fuzzy filter; ↑ ↓ navigate, Enter opens, Esc closes.
 */
(function () {
  "use strict";

  const STATE = { items: null, modal: null, input: null, list: null, focusIdx: 0 };

  function staticDir() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) + "_static" : "_static";
  }
  function docRootUrl() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) : "./";
  }

  function loadIndex() {
    if (STATE.items) return Promise.resolve(STATE.items);
    return new Promise((resolve, reject) => {
      // searchindex.js calls Search.setIndex(...) on load. Shim it.
      const prev = window.Search;
      window.Search = {
        setIndex(idx) {
          const docs = idx.docnames || [];
          const titles = idx.titles || [];
          STATE.items = docs.map((d, i) => ({
            url: docRootUrl() + d + ".html",
            title: titles[i] || d,
            doc: d,
          }));
          window.Search = prev;
          resolve(STATE.items);
        },
      };
      const s = document.createElement("script");
      s.src = `${staticDir()}/searchindex.js`;
      s.onerror = () => reject(new Error("searchindex.js failed to load"));
      document.head.appendChild(s);
    });
  }

  function ensureModal() {
    if (STATE.modal) return STATE.modal;
    const wrap = document.createElement("div");
    wrap.className = "cmdk-overlay";
    wrap.innerHTML = `
      <div class="cmdk-modal" role="dialog" aria-label="Command palette">
        <input class="cmdk-input" type="text" placeholder="Search pages…" autocomplete="off">
        <ul class="cmdk-list" role="listbox"></ul>
      </div>
    `;
    document.body.appendChild(wrap);
    STATE.modal = wrap;
    STATE.input = wrap.querySelector(".cmdk-input");
    STATE.list = wrap.querySelector(".cmdk-list");

    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
    STATE.input.addEventListener("input", render);
    STATE.input.addEventListener("keydown", onKey);
    return wrap;
  }

  function open() {
    ensureModal();
    STATE.modal.classList.add("open");
    STATE.input.value = "";
    STATE.focusIdx = 0;
    loadIndex().then(render).catch(console.error);
    setTimeout(() => STATE.input.focus(), 10);
  }

  function close() {
    if (STATE.modal) STATE.modal.classList.remove("open");
  }

  function render() {
    if (!STATE.items) return;
    const q = STATE.input.value.trim().toLowerCase();
    const items = !q ? STATE.items.slice(0, 12)
      : STATE.items.filter(it =>
          it.title.toLowerCase().includes(q) || it.doc.toLowerCase().includes(q)
        ).slice(0, 12);
    STATE.list.innerHTML = items.map((it, i) =>
      `<li role="option" class="cmdk-item${i === STATE.focusIdx ? " active" : ""}"
           data-url="${it.url}">${it.title}<span class="cmdk-doc">${it.doc}</span></li>`
    ).join("");
    STATE.list.querySelectorAll("li").forEach((li, i) => {
      li.addEventListener("mouseenter", () => { STATE.focusIdx = i; updateFocus(); });
      li.addEventListener("click", () => { window.location = li.dataset.url; });
    });
  }

  function updateFocus() {
    STATE.list.querySelectorAll("li").forEach((li, i) =>
      li.classList.toggle("active", i === STATE.focusIdx));
    const cur = STATE.list.querySelector("li.active");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function onKey(e) {
    const n = STATE.list.children.length;
    if (e.key === "Escape") { close(); }
    else if (e.key === "Enter") {
      const cur = STATE.list.querySelector("li.active");
      if (cur) window.location = cur.dataset.url;
    }
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      STATE.focusIdx = (STATE.focusIdx + 1) % n; updateFocus();
    }
    else if (e.key === "ArrowUp") {
      e.preventDefault();
      STATE.focusIdx = (STATE.focusIdx - 1 + n) % n; updateFocus();
    }
  }

  document.addEventListener("keydown", (e) => {
    const isMac = navigator.platform.toLowerCase().includes("mac");
    const trigger = (isMac && e.metaKey) || (!isMac && e.ctrlKey);
    if (trigger && (e.key === "k" || e.key === "K")) { e.preventDefault(); open(); }
  });

  window.tmhpCmdK = { open, close };
})();
```

- [ ] **Step 2: Wire `cmdk.js` into `page.html` (load before global.js) and `global.js` (idempotent)**

In `_templates/page.html`:

```jinja
{%- block extrahead -%}
{{ super() }}
<script src="{{ pathto('_static/js/core/glossary.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/cmdk.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/global.js', 1) }}" defer></script>
{%- endblock -%}
```

`global.js`: the cmdk module installs its own document-level keydown listener inside its IIFE, so no extra wiring is needed in `global.js`. Add only a marker comment:

```javascript
// ⑧ cmdk — module self-wires its keydown listener; nothing to do here.
```

- [ ] **Step 3: CSS**

Under `/* --- Cmd+K palette (⑧) --- */`:

```css
.cmdk-overlay {
    position: fixed; inset: 0; z-index: 10000;
    background: rgba(0,0,0,0.35);
    display: none; align-items: flex-start; justify-content: center;
    padding-top: 12vh;
}
.cmdk-overlay.open { display: flex; }
.cmdk-modal {
    width: min(560px, 92vw);
    background: var(--rx-gray-1);
    border: 1px solid var(--rx-hairline);
    border-radius: var(--rx-radius-4);
    box-shadow: 0 24px 64px rgba(0,0,0,0.18);
    overflow: hidden;
}
.cmdk-input {
    width: 100%; padding: 14px 18px; border: 0;
    font-family: var(--rx-font-sans); font-size: var(--rx-fs-4);
    background: transparent; color: var(--rx-ink);
    border-bottom: 1px solid var(--rx-hairline);
}
.cmdk-input:focus { outline: none; }
.cmdk-list {
    list-style: none; margin: 0; padding: 4px;
    max-height: 50vh; overflow-y: auto;
}
.cmdk-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 14px; border-radius: var(--rx-radius-2);
    font-size: var(--rx-fs-3); cursor: pointer;
    color: var(--rx-ink);
}
.cmdk-item .cmdk-doc {
    font-family: var(--rx-font-mono); font-size: var(--rx-fs-1);
    color: var(--rx-ink-muted);
}
.cmdk-item.active { background: var(--rx-accent-3); color: var(--rx-accent-11); }
```

- [ ] **Step 4: Build + verify**

Run: `cd docs && uv run make html`
Open any built page. Press ⌘K (or Ctrl+K). Expected:
- Modal opens centered.
- Typing filters page titles.
- ↑/↓ navigates highlights.
- Enter navigates; Esc closes; clicking outside closes.

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`

- [ ] **Step 5: Commit**

```bash
git add docs/source/_static/js/core/cmdk.js \
        docs/source/_static/js/core/global.js \
        docs/source/_templates/page.html \
        docs/source/_static/css/custom.css
git commit -m "feat(docs/core): command palette (Cmd+K)

Adds a keyboard-driven modal that reads page titles from Sphinx's
existing _static/searchindex.js — no extra index built. ⌘K on Mac,
Ctrl+K elsewhere; ↑/↓ navigation, Enter opens, Esc closes. The
existing Sphinx /search/ page is untouched and remains the no-JS
fallback."
```

---

### Task 14: ⑨ Reading progress + scroll-spy + anchor copy

**Files:**
- Create: `docs/source/_static/js/core/reading-progress.js`
- Create: `docs/source/_static/js/core/scroll-spy.js`
- Create: `docs/source/_static/js/core/anchor-copy.js`
- Modify: `docs/source/_static/js/core/global.js`
- Modify: `docs/source/_templates/page.html`
- Modify: `docs/source/_static/css/custom.css`

- [ ] **Step 1: Implement `reading-progress.js`**

```javascript
/**
 * ⑨a Reading progress: fills a 3-px bar fixed to the top of the viewport
 * based on the visible portion of the main article (`.yue`), not the
 * whole page (sidebar / TOC fixed regions don't affect reading state).
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;

  const bar = document.createElement("div");
  bar.className = "reading-progress";
  document.body.appendChild(bar);

  function update() {
    const top = article.getBoundingClientRect().top + window.scrollY;
    const height = article.offsetHeight;
    const visible = Math.min(
      Math.max(window.scrollY - top + window.innerHeight, 0),
      height
    );
    const pct = height > 0 ? (visible / height) * 100 : 0;
    bar.style.width = `${pct}%`;
  }
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
})();
```

- [ ] **Step 2: Implement `scroll-spy.js`**

```javascript
/**
 * ⑨b Scroll spy: highlights the right-side TOC entry corresponding to
 * the section heading currently most in view. Uses IntersectionObserver
 * on h2 / h3 inside the article.
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;
  const toc = document.querySelector(".sy-rside, nav.toc, .toc-list");
  if (!toc) return;

  const headings = article.querySelectorAll("h2[id], h3[id]");
  if (!headings.length) return;

  const linkByHash = new Map();
  toc.querySelectorAll('a[href*="#"]').forEach(a => {
    const hash = a.getAttribute("href").split("#")[1];
    if (hash) linkByHash.set(hash, a);
  });
  if (!linkByHash.size) return;

  let last = null;
  const obs = new IntersectionObserver((entries) => {
    const hit = entries.filter(e => e.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (!hit) return;
    const link = linkByHash.get(hit.target.id);
    if (link && link !== last) {
      if (last) last.classList.remove("is-active");
      link.classList.add("is-active");
      last = link;
    }
  }, { rootMargin: "-30% 0px -60% 0px", threshold: 0 });

  headings.forEach(h => obs.observe(h));
})();
```

- [ ] **Step 3: Implement `anchor-copy.js`**

```javascript
/**
 * ⑨c Anchor copy: shows a small # button next to each h2/h3 on hover;
 * clicking it copies the absolute URL with anchor to the clipboard.
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;

  article.querySelectorAll("h2[id], h3[id]").forEach(h => {
    const btn = document.createElement("button");
    btn.className = "anchor-copy";
    btn.type = "button";
    btn.textContent = "#";
    btn.setAttribute("aria-label", `Copy link to ${h.textContent.trim()}`);
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const url = `${window.location.origin}${window.location.pathname}#${h.id}`;
      try {
        await navigator.clipboard.writeText(url);
        btn.classList.add("copied");
        setTimeout(() => btn.classList.remove("copied"), 1200);
      } catch {
        // Fallback: select-range copy
        const r = document.createRange();
        const tmp = document.createElement("span");
        tmp.textContent = url; document.body.appendChild(tmp);
        r.selectNode(tmp);
        getSelection().removeAllRanges();
        getSelection().addRange(r);
        document.execCommand("copy");
        tmp.remove();
      }
    });
    h.appendChild(btn);
  });
})();
```

- [ ] **Step 4: Load the three modules + wire from global.js**

`_templates/page.html`:

```jinja
{%- block extrahead -%}
{{ super() }}
<script src="{{ pathto('_static/js/core/glossary.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/cmdk.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/reading-progress.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/scroll-spy.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/anchor-copy.js', 1) }}" defer></script>
<script src="{{ pathto('_static/js/core/global.js', 1) }}" defer></script>
{%- endblock -%}
```

`global.js` already lets each module self-wire; no change needed here beyond a marker comment.

- [ ] **Step 5: CSS**

Under `/* --- Reading progress + scroll-spy + anchor copy (⑨) --- */`:

```css
.reading-progress {
    position: fixed; top: 0; left: 0; height: 3px; width: 0;
    background: linear-gradient(90deg, var(--rx-accent-9), var(--rx-violet-9));
    z-index: 10001; pointer-events: none;
    transition: width 80ms ease-out;
}
.sy-rside a.is-active,
nav.toc a.is-active,
.toc-list a.is-active {
    color: var(--rx-accent-11);
    background: var(--rx-accent-3);
    border-radius: var(--rx-radius-2);
    padding: 0 6px;
    font-weight: var(--rx-fw-medium);
}
.anchor-copy {
    background: transparent; border: 0; cursor: pointer;
    color: var(--rx-ink-muted); margin-left: 6px;
    font-size: 0.85em; opacity: 0; transition: opacity 100ms;
    padding: 0 4px; border-radius: var(--rx-radius-1);
}
h2:hover .anchor-copy, h3:hover .anchor-copy { opacity: 1; }
.anchor-copy:hover { color: var(--rx-accent-11); background: var(--rx-accent-3); }
.anchor-copy.copied::after {
    content: " copied"; font-size: 0.8em; color: var(--rx-green-11);
}
```

- [ ] **Step 6: Build + verify**

Run: `cd docs && uv run make html`
On any built page with a long article:
- Scroll: progress bar fills.
- Right TOC entry highlights as you scroll across sections.
- Hover a heading: `#` button appears; clicking copies the URL and shows a "copied" tag.

Run: `cd docs && uv run sphinx-build -W --keep-going -b html source build/html`

- [ ] **Step 7: Final cross-page check — no external CDN hits**

Open any page in DevTools → Network → reload with cache disabled. Confirm zero requests to hosts other than `localhost` / `127.0.0.1` / `file://` (depending on serving method).

- [ ] **Step 8: Commit**

```bash
git add docs/source/_static/js/core/reading-progress.js \
        docs/source/_static/js/core/scroll-spy.js \
        docs/source/_static/js/core/anchor-copy.js \
        docs/source/_static/js/core/global.js \
        docs/source/_templates/page.html \
        docs/source/_static/css/custom.css
git commit -m "feat(docs/core): reading progress, scroll-spy, anchor copy

Three small global enhancements that operate on every page:

* A 3-px gradient bar at the top fills 0–100 % with the scroll-through
  of the article (not the whole page, so the fixed sidebar/TOC don't
  inflate the denominator).
* The right TOC entry for the section currently most in view gets a
  Radix-accent pill (matches the sidebar active pill pattern).
* Hovering an h2 / h3 reveals a # button that copies the absolute URL
  with the heading anchor to the clipboard.

All three are additive; with JS disabled the page renders identically
to today's docs."
```

---

## Self-review checklist

Before declaring the plan complete, the executor (or planner) should walk this list:

**Spec coverage:**
- [ ] § Why & guiding principles 1–5 — preserved (no runtime CoolProp, no external CDN, fallback intact, one pattern = one rollback).
- [ ] § File layout — Task 1 / 4 / 11 / 13 / 14 lay it down end-to-end.
- [ ] All 9 patterns (①–⑨) — Tasks 6 / 7 / 8 / 9 / 10 / 11 / 12 / 13 / 14 respectively.
- [ ] § Execution order — Tasks 1–14 mirror the 14 commits in the spec.
- [ ] § Validation strategy — every task runs sphinx-build -W and the per-task smoke list.
- [ ] § Rollback procedure — single-commit grain preserved.

**Placeholder scan:** No "TBD", "TODO", "..", "fill in" remaining. (One exception: the prose-edit step in Task 11 / Step 4 says to wrap *first occurrences*, leaving the exact term position up to the executor — this is acceptable because it's a markup judgement call, not a missing requirement.)

**Type consistency:** `tmhpPlot.tokens()` / `tmhpPlot.loadJson()` / `tmhpPlot.bilinear()` / `tmhpPlot.staticDir()` are defined in Task 6 Step 1 and consumed identically in Tasks 7, 8. The CustomEvent names `tmhp:parity-selected` and `tmhp:table-selected` are paired correctly between Tasks 7 and 9. `validation-table-static` class is referenced in both Task 9 / Step 3 (added) and Task 9 / Step 1 (read by JS).

---

## Execution checkpoints

- After Task 5 (end of Phase 0): `make html` succeeds clean; cycle-architecture interactive still works; zero external CDN requests detected.
- After Task 10 (end of Phase 1): every interactive widget on each affected page works; JS-disabled smoke test passes everywhere; sphinx-build -W still 0 warnings.
- After Task 14: full DevTools Network audit on five representative pages (index, concepts/cycle-architecture, tutorials/visualize-the-cycle, validation/index, models/ashpb) shows zero external hosts; all interactions work end-to-end; tour-able by the user.

If a single pattern misbehaves after the user reviews the deployed branch, `git revert <commit-sha>` removes only that pattern. If the user wants none of it, `git checkout main && git branch -D docs/interactive-ux` returns to today's docs.
