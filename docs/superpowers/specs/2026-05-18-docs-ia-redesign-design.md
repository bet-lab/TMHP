# Docs IA redesign — design

**Date:** 2026-05-18
**Status:** Approved

## Why

The current docs grew organically: `concepts/cycle-architecture.rst`
tries to be both the "shared abstraction" page and the "how each
source/sink differs" page, so system-specific details (g-function,
borehole field geometry, outdoor-coil ε-NTU) keep leaking into a
page that is supposed to be source-agnostic. `tutorials/compose-
subsystems.rst` describes how `ASHPB_STC_preheat` /
`ASHPB_PV_ESS` are used, but those classes are first-class library
artefacts and don't appear in any reference page next to their base
class. `api/models/` is reference-only — there is no single page
where a reader can land and learn "what is ASHPB and how do I use
it end to end."

The redesign reorganises the nav around the library's actual
identity: **ASHP is the core case, and modular composition (other
sources, STC / PV / ESS subsystems) is how that core extends**. The
nav surfaces this directly.

## Top-level TOC

Six top-level sections, replacing the current five:

```
1. Getting Started     — first-touch flow, ASHPB only
2. Concepts            — abstractions, source-agnostic only
3. Models              — NEW; per-model 1-stop pages
4. Tutorials           — cross-cutting cookbook
5. API Reference       — helpers / support / components only
6. Validation          — unchanged
```

Two alternatives were considered and rejected:

- Folding Validation into `Models > ASHPB`. Rejected because
  validation is a primary credibility signal for a physics-based
  library and should remain a top-level entry point.
- Folding API Reference into `Models`. Rejected because helper /
  support reference (`heat-transfer`, `calc_util`, `thermodynamics`,
  …) is cross-cutting; splitting it per model would scatter it.

## Each section

### Getting Started (3 pages, no structural change)

- `installation.rst`
- `quickstart.rst` — ASHPB only
- `first-dynamic-simulation.rst` — ASHPB only

The first-touch flow stays ASHP-only by design. A reader who wants
to try GSHP / WSHP follows the link out to the Models section.

### Concepts (4 pages, slimmed)

- `why-physics-based.rst` — unchanged (keeps the COP-vs-source-temp
  figure)
- `cycle-architecture.rst` — **slimmed**. Removes the per-source
  detail table, the g-function explanation, and any
  system-specific mechanic. Keeps the shared closed-cycle
  abstraction (compressor → condenser → expansion → evaporator)
  and the Cytoscape interactive diagram.
- `failure-reason-semantics.rst` — unchanged
- `refrigerant-and-coolprop.rst` — unchanged

### Models (NEW top-level, 5 pages, 1-stop)

Each page follows a fixed template:

1. **Overview** — what this model is, when to use it
2. **Base usage** — `analyze_steady` / `analyze_dynamic` patterns
3. **Source-side mechanics** — system-specific (outdoor coil for
   ASHP, g-function for GSHP, prescribed inlet for WSHP)
4. **Sink-side mechanics** — DHW tank or building load
5. **Composed variants** — `*_STC_preheat`, `*_STC_tank`,
   `*_PV_ESS` usage patterns (where the class hierarchy supports
   them)
6. **API reference** — autodoc
7. **Validation** — present on `ashpb` (links to the Samsung parity);
   absent elsewhere until benchmarks exist.

Pages:

- `models/ashpb.rst` — ASHPB + ASHPB_STC_preheat + ASHPB_STC_tank
  + ASHPB_PV_ESS. Includes the PV/ESS energy-balance figure.
- `models/gshpb.rst` — GSHPB + GSHPB_STC_preheat + GSHPB_STC_tank
  + GSHPB_PV_ESS. Includes the g-function figure.
- `models/wshpb.rst` — WSHPB.
- `models/ashp.rst` — space-conditioning ASHP.
- `models/gshp.rst` — space-conditioning GSHP.

### Tutorials (3 pages, cross-cutting)

- `swap-refrigerant.rst` — unchanged (refrigerant comparison P–h
  figure already integrated)
- `realistic-dynamic-simulation.rst` — adds the 24-hour dynamic
  timeseries figure
- `visualize-the-cycle.rst` — unchanged (P–h + T–s figures already
  integrated)
- `compose-subsystems.rst` — **deleted**; content folded into
  `models/ashpb.rst` and `models/gshpb.rst`

### API Reference (helpers / support / components only)

- `api/support/` — unchanged: `heat-transfer`, `thermodynamics`,
  `calc_util`, `visualization`, `cop`, `dhw`, `weather`, …
- `api/subsystems/` — **NEW**: `SolarThermalCollector`,
  `PhotovoltaicSystem`, `EnergyStorageSystem` component classes
- `api/models/` — **deleted**; content promoted to top-level Models

### Validation (unchanged)

- `validation/index.rst` — Samsung ASHPB parity. Future GSHP /
  WSHP entries follow the same pattern.

## Content migration map

| From | To |
|---|---|
| `concepts/cycle-architecture.rst` § "The borehole g-function" | `models/gshpb.rst` § Source-side mechanics |
| `concepts/cycle-architecture.rst` § Source/Sink tables | `models/*.rst` § Overview + source-side intro |
| `tutorials/compose-subsystems.rst` (STC content) | `models/ashpb.rst` § Composed variants > STC preheat |
| `tutorials/compose-subsystems.rst` (PV+ESS reference) | `models/ashpb.rst` § Composed variants > PV+ESS |
| `api/models/ashpb.rst` | `models/ashpb.rst` (promoted, expanded) |
| `api/models/gshpb.rst` | `models/gshpb.rst` (promoted, expanded) |
| `api/models/wshpb.rst` | `models/wshpb.rst` (promoted, expanded) |
| `api/models/space-conditioning.rst` | split into `models/ashp.rst` + `models/gshp.rst` |
| (new content) `ASHPB_PV_ESS` usage pattern | `models/ashpb.rst` § Composed variants > PV+ESS |

## Implementation phases

Phases ordered by dependency. Each phase ends at a `sphinx-build
-W` clean state so partial work doesn't break the build.

### Phase 1 — Scaffold Models section
- Create `docs/source/models/` with `index.rst` + five empty
  skeleton pages (overview + section headings, no body yet)
- Add `models` to `conf.py` `nav_links` and the index landing-page
  grid
- Add the `models/index` toctree entry to `index.rst`
- Verify build is clean

### Phase 2 — Migrate api/models content
- Copy each `api/models/*.rst` body into the matching
  `models/*.rst` page under the new template's "API reference"
  section
- Split `api/models/space-conditioning.rst` into two pages —
  `models/ashp.rst` (ASHP class) and `models/gshp.rst` (GSHP
  class); the shared "space-conditioning" framing moves into each
  page's Overview section
- Update intersphinx references (`:doc:`../api/models/...``) to
  point at the new paths
- Delete `api/models/` and remove its `toctree` entry from
  `api/index.rst`
- Verify build is clean (no broken cross-references)

### Phase 3 — Migrate compose-subsystems into Models
- Move the STC content into `models/ashpb.rst` and
  `models/gshpb.rst` § Composed variants > STC
- Move the PV+ESS reference into the same `Composed variants`
  block. Embed the existing
  `_static/pv_ess_energy_balance.svg`
- Delete `tutorials/compose-subsystems.rst` and remove from the
  tutorials toctree + landing-grid card
- Verify build is clean

### Phase 4 — Slim cycle-architecture
- Remove the per-source detail table and the "The borehole
  g-function" subsection from `concepts/cycle-architecture.rst`
- Move the g-function figure embed into `models/gshpb.rst`
- Verify build is clean

### Phase 5 — New api/subsystems pages
- Create `api/subsystems/index.rst` + per-class pages for
  `SolarThermalCollector`, `PhotovoltaicSystem`,
  `EnergyStorageSystem`
- Wire into `api/index.rst` toctree

### Phase 6 — Polish
- Update `getting-started/*.rst` "next steps" links to point at
  the new Models pages where relevant
- Update `index.rst` landing-page grid cards: add a "Models"
  card; keep existing six other cards (Getting Started / Concepts
  / Tutorials / API Reference / Validation / Visualize) as-is.
  Update the `nav_links` array in `conf.py` to include Models in
  the same order as the toctree
- Run a full link audit (sphinx-build `-W --keep-going -b
  linkcheck`)
- Final `sphinx-build -W` clean

## Out of scope

- Re-authoring the existing figure scripts (the seven figures
  generated in the prior session stay as they are)
- Translating new content into Korean
- Renaming top-level URLs in a way that breaks deep links (we
  keep `api/`, `concepts/`, etc. at their current paths; only the
  *contents* shift and `models/` is added new)

## Risks and mitigations

- **Risk:** `git log --follow` history is lost for moved files
  (`api/models/ashpb.rst` → `models/ashpb.rst`).
  **Mitigation:** Use `git mv` for each moved file in the phase
  it's touched so blame history is preserved.

- **Risk:** Six top-level nav entries may feel heavy on small
  viewports.
  **Mitigation:** The Shibuya theme collapses nav into a
  hamburger on narrow viewports; six entries fit comfortably on
  desktop.

- **Risk:** Readers with bookmarks to `api/models/ashpb.html`
  hit 404 after the move.
  **Mitigation:** Add a one-line redirect notice in the GitHub
  Pages 404 (already configured via `sphinx-notfound-page`); or
  optionally add a stub page at the old path linking to the new
  one. Decision deferred until Phase 2 is implemented.
