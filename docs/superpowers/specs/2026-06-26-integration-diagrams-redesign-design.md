# Integration docs — diagram system redesign (design)

**Date:** 2026-06-26
**Scope:** `docs/source/integrations/` (index, fmu, energyplus-python) and a new
shared diagram engine under `docs/source/_static/`.

## Problem

The integration pages are the selling point of TMHP-as-an-integration-package,
but they carried only one figure: a hand-wired Cytoscape.js "integration
boundary" graph embedded as raw HTML in `index.rst`. It was heavy
(graph-layout engine + ~340 lines of inline JS/CSS per page), its auto-layout
read as noisy, it did not adapt to the docs dark theme, and the two per-adapter
pages (`fmu`, `energyplus-python`) had no diagrams at all — just prose, tables,
and an ASCII arrow.

## Decision

Replace the single interactive widget with a **set of six purpose-built
diagrams** driven by one small geometry engine. The style is *hybrid*: a
static, dark-mode-aware SVG base (the gold-standard pattern of
`_static/cycle-architecture.svg`) with light, optional interaction — lane focus
on the hero, a step-through on the two protocol sequences. No graph-layout
engine; connectors are computed from box geometry so every line leaves an edge
centre.

### Diagram inventory

| key (`data-diagram`) | page | what it shows |
| --- | --- | --- |
| `hero` | index | "One core, three drivers" — Python / EnergyPlus / FMI converge on one core through different seams (lane-focus tabs) |
| `fmu-seq` | fmu | the `do_step` co-simulation protocol, step-through |
| `fmi-compare` | fmu | FMI 2.0 vs 3.0 — two titled adapter cards that both *wrap* one shared `step()` kernel |
| `fmu-example` | fmu | composite co-sim use case (envelope FMU + TMHP FMU + controller) |
| `ep-seq` | energyplus-python | the plant-callback protocol, step-through |
| `ep-example` | energyplus-python | cycle-resolved swap-in use case + per-refrigerant COP |

All content is grounded in the real adapters
(`src/tmhp/integrations/{fmu,fmu3,energyplus_plugin}.py`): the boundary
variables, the public seams (`analyze_steady` vs `step`), the FMI 2.0/3.0
mechanics differences, and the per-call processing notes are taken from the code,
not invented.

## Architecture

- **`_static/css/integration-diagrams.css`** — design-token SSOT. Role colours
  mirror `cycle-architecture.svg` (indigo = shared core, violet = public
  seam/API, amber = external host). All tokens are scoped under `.tmhp-diagram`
  so they never collide with the theme's `--sy-*` or the site's `--rx-*`.
  Dark mode is **CSS-only**: the token block is flipped both for the OS
  preference (`@media (prefers-color-scheme: dark)`, gated on
  `html:not([data-color-mode="light"])`) and for Shibuya's manual toggle
  (`html[data-color-mode="dark"]`). No dark-mode JS.
- **`_static/js/widgets/integration-diagrams.js`** — the geometry engine
  (`box`, edge-centre `A()`, `roundPath`, `mergeTo`/`heroMerge`, `sequence`,
  `arrowDefs`) plus the six figure builders. On `DOMContentLoaded` it finds
  every `.tmhp-diagram[data-diagram]` on the page and renders the matching
  builder (controls + `<svg>` + caption) into it. Interaction state is held in
  per-container closures, so multiple diagrams on a page never clash.
- **`conf.py`** — registers the CSS in `html_css_files` and the JS in
  `html_js_files` (deferred, content-hashed like the other interactive modules).
- **`.rst`** — each page embeds a container with one line of raw HTML, e.g.
  `<div class="tmhp-diagram" data-diagram="hero"></div>`. No per-page JS/CSS.

## How to edit or add a diagram

1. Edit the corresponding builder in `integration-diagrams.js` (geometry is
   plain coordinates; connectors use the `A()`/`roundPath`/`mergeTo` helpers so
   they stay anchored to edge centres).
2. To add a new figure: write a builder, add it to the `DIAGRAMS` map, and drop
   a `<div class="tmhp-diagram" data-diagram="…"></div>` into the page.
3. Keep every colour as a `var(--…)` token so dark mode keeps working; never
   hard-code a hex in a builder except for one-off neutral shades that already
   have a dark counterpart.

## Accessibility / robustness

- Each SVG carries `role="img"` + `<title>`/`<desc>`.
- `prefers-reduced-motion` disables the (subtle) motion via CSS.
- Sequences render the full ladder at rest; stepping only dims the rest, so the
  diagram is readable even with JS interaction unused.

## Verification

`uv run --group docs sphinx-build` builds clean (no new warnings); all six
containers render to SVG in the built site, and the figures adapt correctly to
the manual dark toggle and the OS dark preference.
