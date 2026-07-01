# Docs interactive UX — design

**Date:** 2026-05-18
**Status:** Shipped (with partial roll-back — see note below)

> **Shipped state (2026-05-19)**
>
> Six of the nine designed patterns landed and survived a polish review.
> Three were rolled back after a live look on the deployed branch:
>
> | # | Pattern | Status | Note |
> |---|---|---|---|
> | ① | Live P–h chart | shipped | refrigerant selector, sliders, COP readout inside SVG |
> | ② | Interactive parity plot | reverted | static SVG (matplotlib) retained, JS overlay removed |
> | ③ | 24-hour timeseries scrub | reverted | not enough information density to justify the JS |
> | ④ | Filterable validation table | shipped | compares COP_cat vs COP_pred, not Q (audit fix) |
> | ⑤ | Composition variant tabs | shipped | ASHPB and GSHPB only (others have no STC/PV variants) |
> | ⑥ | Inline glossary popovers | shipped | viewport-aware placement, hover-bridge timer |
> | ⑦ | Hero motion + counters | reverted | decorative, didn't earn its place |
> | ⑧ | Cmd+K palette | shipped | discoverability hint added next to header search |
> | ⑨a | Reading progress bar | shipped | 3 px gradient, top of viewport |
> | ⑨b | Scroll-spy right-TOC | shipped | active section pill |
> | ⑨c | Heading # anchor copy | reverted | hover noise on every h2/h3 |
>
> Foundation tasks (1–5: build-time CoolProp JSON, self-hosted D3 +
> cytoscape, Makefile wiring, global JS entry) all shipped. The
> design body below describes the original intent — see git log for
> the actual implementation path.

## Why

The current docs render cleanly with the Shibuya theme + Radix-DNA
token layer + sphinx-design, and one page (`concepts/cycle-architecture`)
is already richly interactive via Cytoscape. Everything else is
static: SVG figures for P–h diagrams, parity plots, 24-hour
timeseries, and cycle architectures; static tables for the
15-point validation set; static landing cards. A reader cannot
swap a refrigerant on the P–h chart, hover a parity point to see
the case behind it, scrub the 24h timeseries to read off a value,
or jump between pages without leaving the keyboard.

This design adds nine interactive patterns on top of the existing
docs without rewriting any content. Each pattern is a self-contained
unit (one JS file + optional JSON data + one mount in an `.rst`
file) so any pattern that doesn't pan out can be rolled back by
reverting a single commit.

## Guiding principles

1. **Function-led, polish-trimmed.** Interactions earn their place
   by helping the reader understand the library faster. Decorative
   motion is limited to one place (the landing hero).

2. **Static fallback intact.** The existing SVG figures, prose, and
   code blocks stay untouched. Interactive layers mount onto fresh
   DOM nodes alongside them. If JS fails to load (corp firewall,
   ad blocker, slow network), the page degrades to today's static
   docs — never to a broken state.

3. **One pattern, one rollback.** Each of the nine patterns ships
   as 1–3 commits scoped to that pattern. Reverting that commit
   range removes the pattern cleanly. No shared state across
   patterns except the build-time data files (which are reused by
   only two patterns).

4. **No external network at runtime.** All libraries (D3 cherry-pick,
   existing Cytoscape) are self-hosted under `_static/js/lib/`. All
   data is generated at build time and committed as JSON under
   `_static/data/`. Docs build and run with zero internet dependency.

5. **Build-time thermodynamic data.** CoolProp is invoked from
   Python at build time to pre-compute saturation domes, isotherms,
   and cycle-state grids. The browser reads JSON and renders with
   D3. Runtime CoolProp-WASM is rejected: ~3 MB payload, slow on
   mobile, fails behind some corp proxies, and offers a "calculator"
   capability the docs don't actually need (the library itself is
   the calculator).

## File layout

```
docs/source/
├── _static/
│   ├── css/custom.css              # existing — append global layer at end
│   ├── js/                         # new
│   │   ├── lib/
│   │   │   ├── d3.v7.custom.min.js     # cherry-picked, ~50 KB
│   │   │   └── cytoscape.min.js        # moved from CDN
│   │   ├── core/
│   │   │   ├── global.js               # entry — imports/wires modules below
│   │   │   ├── reading-progress.js     # ⑨
│   │   │   ├── scroll-spy.js           # ⑨
│   │   │   ├── anchor-copy.js          # ⑨
│   │   │   ├── glossary.js             # ⑥
│   │   │   └── cmdk.js                 # ⑧
│   │   ├── plots/
│   │   │   ├── ph-chart.js             # ①
│   │   │   ├── parity-plot.js          # ②
│   │   │   └── timeseries-scrub.js     # ③
│   │   └── widgets/
│   │       ├── validation-table.js     # ④
│   │       └── hero-motion.js          # ⑦
│   └── data/                       # new, build-time generated
│       ├── glossary.json
│       ├── refrigerants/{R32,R290,R134a,R1234yf}.json
│       ├── validation-points.json
│       └── timeseries-24h.json
├── _scripts/                       # new
│   ├── gen_refrigerant_data.py
│   ├── gen_validation_data.py
│   ├── gen_timeseries_data.py
│   └── gen_glossary.py
└── _templates/
    └── page.html                   # existing — add one <script> tag for global layer
```

## Patterns

The nine patterns and their bindings:

### ① Live P–h chart (refrigerant selector)

- **Location:** `tutorials/visualize-the-cycle.rst`,
  `concepts/refrigerant-and-coolprop.rst`,
  `tutorials/swap-refrigerant.rst`
- **Mount:**
  ```rst
  .. raw:: html
     <div id="ph-chart-mount"
          data-refrigerants="R32,R290,R134a,R1234yf"
          data-default="R32"></div>
     <script src="../_static/js/plots/ph-chart.js"></script>
  ```
- **Data:** `_static/data/refrigerants/{ref}.json` — saturation dome
  (P, h_liquid, h_vapor at ~80 reduced-temperature samples), isotherms
  at standard temperatures, cycle solution at a (T_evap × T_cond) grid
  of 21 × 21 with default superheat/subcooling.
- **UI:** refrigerant dropdown, T_evap slider (−20 … +20 °C),
  T_cond slider (+25 … +65 °C), optional superheat/subcooling.
  Right-side card shows COP, m_dot, Q_cond at the chosen point.
  Slider movement = bilinear interpolation across the cycle grid.
- **Library:** D3
- **Fallback:** existing `mollier_cycle_R32.svg` shown directly below
  (or above) the mount, captioned as the "static reference."

### ② Parity plot — point hover cards

- **Location:** `validation/index.rst`
- **Mount:** `<div id="parity-plot-mount"></div>` + script
- **Data:** `_static/data/validation-points.json` — 15 entries each
  with `{case_id, refrigerant, T_source, T_sink, Q_cat, Q_mod,
  COP_cat, COP_mod, notes}`.
- **UI:** scatter of Q_mod vs Q_cat with y = x reference. Hover →
  fixed-position card with all case fields and a "↳ open in
  validation table" link. Click → highlight the corresponding row
  in ④. Selected state persists until another point or background
  click.
- **Library:** D3
- **Fallback:** existing `validation_parity.svg`.

### ③ 24-hour timeseries scrub

- **Location:** `tutorials/realistic-dynamic-simulation.rst`,
  `getting-started/first-dynamic-simulation.rst`
- **Mount:** `<div id="ts-scrub-mount" data-source="timeseries-24h.json"></div>`
- **Data:** `_static/data/timeseries-24h.json` — 144 points at
  10-minute cadence, each `{t, T_amb, Q_heat, COP, P_cmp, m_dot, ...}`.
- **UI:** three stacked subplots sharing the time axis (T_amb /
  Q_heat / COP). Vertical cursor follows pointer X; side panel
  shows the full row at that timestamp. Optional drag-to-zoom on
  the time axis (range select), double-click resets.
- **Library:** D3
- **Fallback:** existing `dynamic_24h_timeseries.svg`.

### ④ Filterable validation table

- **Location:** `validation/index.rst` (below ②)
- **Mount:** the existing rst table is kept in source. A sibling
  `<div id="validation-table-mount"></div>` is added beside it. On
  successful JS hydration, the rst table is hidden (`display:none`)
  and the interactive widget renders.
- **Data:** same `validation-points.json` as ②.
- **UI:** text filter input, refrigerant chip filter row, sortable
  column headers. Row click highlights ②'s corresponding point and
  vice versa. State is in-page only (not URL-persisted).
- **Library:** vanilla JS (no D3).
- **Fallback:** the rst table renders untouched. No-JS readers see
  exactly today's table.

### ⑤ Model composition tabs

- **Location:** `models/{ashpb,gshpb,wshpb,ashp,gshp}.rst`
- **Mount:** uses sphinx-design's `tab-set` directive directly:
  ```rst
  .. tab-set::

     .. tab-item:: Base
        ...
     .. tab-item:: + STC
        ...
     .. tab-item:: + STC stratified
        ...
     .. tab-item:: + PV / ESS
        ...
  ```
- **Data:** none (in-page content only).
- **UI:** sphinx-design renders the tabs natively. Our contribution
  is CSS that aligns the tab strip with Radix-DNA tokens (subtle
  accent underline on active, generous touch targets).
- **Library:** none (sphinx-design + CSS).
- **Fallback:** sphinx-design's static accordion fallback (already
  exists).

### ⑥ Inline glossary popover

- **Location:** global. Activated by `<span class="glossary"
  data-term="...">` markup. The Phase 2 commit that ships this
  pattern includes an initial wrapping pass that adds spans to a
  canonical term set (ε-NTU, COP, EXV, ASHPB / GSHPB / WSHPB / ASHP
  / GSHP, m_dot, dT_evap, η_is, η_vol, η_mech) across the concepts
  pages and the top of each models page. This is markup wrapping,
  not content rewriting.
- **Mount:** none per-page; the global core script scans for
  `.glossary` spans on `DOMContentLoaded`.
- **Data:** `_static/data/glossary.json` — `{ "epsilon-ntu":
  {name, def, link}, "cop": {...}, ... }`.
- **UI:** dotted underline on terms. Hover or keyboard focus opens
  a small popover anchored to the term: term name, definition
  (1–2 lines), "↳ Concepts page" link. Popover dismisses on
  blur/click-outside/Esc.
- **Library:** vanilla JS.
- **Fallback:** the dotted underline remains; no popover.

### ⑦ Landing hero motion + counter

- **Location:** `index.rst`
- **Mount:** the landing's `hero-badges` + `hero-cta` containers
  are replaced with a `.. raw:: html` block that includes the
  metric counters (`<span class="hero-metric" data-target="15">`)
  and a simplified hero-only P–h sketch (a new ~3 KB inline SVG,
  not the existing detailed `mollier_cycle_R32.svg` — the hero
  needs a fast, decorative silhouette, not a labelled diagram).
- **Data:** none.
- **UI:** on first viewport intersection, the P–h sketch fades in
  once (~600 ms), and three metric counters animate from 0 to
  their target (15 benchmark points / 5 model families / 0 fitted
  curves). Replays on hard reload, not on intra-site nav.
  `prefers-reduced-motion` disables both.
- **Library:** vanilla JS (IntersectionObserver +
  requestAnimationFrame).
- **Fallback:** rendered final state shown immediately (no motion).

### ⑧ Command palette (Cmd+K)

- **Location:** global, in `page.html`.
- **Mount:** the global core script injects a hidden modal node
  into `<body>` on load.
- **Data:** the existing `_static/searchindex.js` (Sphinx's own
  search index) is reused — no new index built.
- **UI:** ⌘K (Mac) / Ctrl+K (others) opens a centered modal.
  Search input, result list grouped by page → section, ↑/↓ to
  navigate, Enter to open, Esc to close. Recent pages shown when
  the input is empty.
- **Library:** vanilla JS.
- **Fallback:** Sphinx's built-in `/search/` page is unchanged and
  remains the no-JS path.

### ⑨ Reading progress + scroll-spy + anchor copy

- **Location:** global.
- **Mount:** the global core script injects a 3-px progress bar
  fixed to the top of the viewport, augments the existing right
  TOC with an active-section indicator, and appends a "#" button
  to each `<h2>`/`<h3>` on hover.
- **Data:** none.
- **UI:**
  - Top bar fills 0–100 % as the reader scrolls the article (not
    the page — the right TOC sticks).
  - Right TOC entry for the section currently most in viewport
    gets `.is-active` (Radix accent tint, mirroring the sidebar
    active pill).
  - Hovering a heading reveals a `#` button on the right; click
    copies the absolute URL with anchor to the clipboard and
    flashes a tiny "copied" tag.
- **Library:** vanilla JS (IntersectionObserver, clipboard API).
- **Fallback:** none of the three is added; today's docs are
  unchanged.

## Execution order

The 14 commits land on a `docs/interactive-ux` branch:

**Phase 0 — Foundation (5 commits)**

1. `feat(docs/data): add CoolProp-driven build-time data scripts`
   — `_scripts/gen_*.py` + initial JSONs.
2. `feat(docs/js): self-host cherry-picked D3 v7 bundle`
   — `_static/js/lib/d3.v7.custom.min.js`.
3. `refactor(docs/js): self-host cytoscape for cycle-architecture`
   — move from cdn.jsdelivr to `_static/js/lib/cytoscape.min.js`.
4. `feat(docs/templates): add global JS entry point + base CSS hooks`
   — `page.html` gains a single `<script src="_static/js/core/global.js"
   defer></script>` tag. `global.js` is initially a no-op IIFE; each
   Phase 2 commit imports its module and wires it from `global.js`.
   Reverting a Phase 2 commit removes both the module file and the
   import line, leaving `global.js` cleanly smaller.
5. `build(docs): wire build-time data scripts into Makefile`
   — `python _scripts/gen_*.py` runs before `sphinx-build`.

**Phase 1 — Per-page interactions (5 commits)**

6. `feat(docs/concepts): live P–h chart with refrigerant selector` (①)
7. `feat(docs/validation): interactive parity plot with hover cards` (②)
8. `feat(docs/tutorials): scrubable 24h timeseries` (③)
9. `feat(docs/validation): filterable validation table` (④, depends on
   the data committed in 7)
10. `feat(docs/models): composition variant tabs via sphinx-design` (⑤)

**Phase 2 — Global layer (4 commits)**

11. `feat(docs/core): inline glossary popovers` (⑥)
12. `feat(docs/landing): hero motion + metric counters` (⑦)
13. `feat(docs/core): command palette (Cmd+K)` (⑧)
14. `feat(docs/core): reading progress, scroll-spy, anchor copy` (⑨)

## Validation strategy

For every commit:

- `sphinx-build -W --keep-going` passes with zero warnings. The CI
  uses the same flag, so anything less fails the build.
- Visual smoke test in a local dev browser (the page that hosts the
  interaction, and one unrelated page that doesn't).
- JS-disabled smoke test (`document.documentElement.classList.add('no-js')`
  or DevTools "Disable JavaScript") — fallback must render correctly.
- DevTools Network panel — after Phase 0 commit 3 (cytoscape
  self-host), there must be zero outbound requests to `cdn.jsdelivr.net`
  or any other external host.

## Rollback procedure

To remove a single pattern:

```
git revert <commit-sha>
```

To remove a range (e.g., the entire global layer):

```
git revert <commit-11>..<commit-14>
```

To bail out entirely before merge:

```
git checkout main
git branch -D docs/interactive-ux
```

Branch-only work means `main` is never in a half-done state.

## Out of scope

- **No content rewriting.** Existing rst prose, code blocks, and
  SVG figures are not edited. The only allowed change to prose is
  wrapping select existing terms in `<span class="glossary">` for
  pattern ⑥ — the underlying words remain identical.
- **No new sphinx extensions** beyond what conf.py already lists.
- **No theme fork.** Shibuya stays as-is; all styling rides on
  custom.css and Radix-DNA tokens.
- **No new dependencies in pyproject.toml's runtime group.**
  CoolProp lives in the existing `docs` group only.
- **No analytics / telemetry.** Interactions are purely client-side
  with no event reporting.
- **Existing IA redesign (2026-05-18-docs-ia-redesign-design)** is
  independent of this work; both can land in either order.

## Open follow-ups (not part of this design)

- Search index ranking: Sphinx's default is lexical. If ⑧ ships
  and the result quality is poor, consider a tiny BM25 layer on
  top of the existing index — separate design.
- Refrigerant set: starts with R32 / R290 / R134a / R1234yf. Adding
  R407C, R410A, etc. is a config change to
  `_scripts/gen_refrigerant_data.py` — not part of this commit.
