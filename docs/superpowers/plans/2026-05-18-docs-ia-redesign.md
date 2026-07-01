# Docs IA Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Sphinx docs to surface a new top-level "Models" section, slim `concepts/cycle-architecture` back to a pure abstraction page, and fold composed-subsystem usage into each base model's 1-stop page — without breaking any existing cross-references or the `sphinx-build -W` invariant.

**Architecture:** Six phases ordered by dependency. Each phase ends at a clean `sphinx-build -W --keep-going -b html` build so partial work never wedges the docs. Phase 1 scaffolds empty Models pages; Phase 2 promotes `api/models/*` content into them; Phase 3 absorbs `tutorials/compose-subsystems` into the relevant Models pages; Phase 4 slims `concepts/cycle-architecture`; Phase 5 polishes nav + landing page; Phase 6 finalises with linkcheck.

**Tech Stack:** Sphinx 7+, Shibuya theme, MyST-parser, sphinx-design, dartwork-mpl figures (already integrated).

---

## Spec reference

Design spec: [docs/superpowers/specs/2026-05-18-docs-ia-redesign-design.md](../specs/2026-05-18-docs-ia-redesign-design.md)

Note: the spec mentions a "Phase 5: New api/subsystems pages." `api/support/subsystems.rst` already exists with `SolarThermalCollector`, `PhotovoltaicSystem`, `EnergyStorageSystem`, `UVLamp`, and `uv_treatment` autoclass blocks — so this plan **omits the spec's Phase 5** and renumbers downstream phases.

---

## File map

**Creating:**
- `docs/source/models/index.rst`
- `docs/source/models/ashpb.rst`
- `docs/source/models/gshpb.rst`
- `docs/source/models/wshpb.rst`
- `docs/source/models/ashp.rst`
- `docs/source/models/gshp.rst`

**Modifying:**
- `docs/source/index.rst` — add Models grid card + toctree entry
- `docs/source/conf.py` — add Models to `nav_links`
- `docs/source/concepts/cycle-architecture.rst` — slim down
- `docs/source/api/index.rst` — drop Models section + grid cards
- `docs/source/tutorials/index.rst` — drop `compose-subsystems` from grid + toctree
- `docs/source/getting-started/first-dynamic-simulation.rst` — point "next moves" at Models
- `docs/source/tutorials/realistic-dynamic-simulation.rst` — embed the 24h dynamic figure (figure file already exists)

**Deleting (after content has been moved):**
- `docs/source/api/models/` (entire directory)
- `docs/source/tutorials/compose-subsystems.rst`

---

## Phase 1 — Scaffold the Models section

### Task 1.1: Create the Models directory and index page

**Files:**
- Create: `docs/source/models/index.rst`

- [ ] **Step 1: Create the models directory**

```bash
mkdir -p docs/source/models
```

- [ ] **Step 2: Write the Models landing page**

Create `docs/source/models/index.rst`:

```rst
======
Models
======

System-level heat pump models — the classes you instantiate directly.
Each page below is a 1-stop reference for one model family: how it
plugs the shared refrigerant cycle into a specific source / sink
pairing, what the system-specific mechanics look like, how to compose
subsystems (STC, PV + ESS) on top, and the full API reference.

ASHPB is the most commonly used model and the one Getting Started
walks you through. The remaining pages mirror the same template so
moving between source families feels uniform.

.. grid:: 2
    :gutter: 3

    .. grid-item-card:: Air-source heat pump boiler
        :link: ashpb
        :link-type: doc

        ASHPB core + STC preheat, STC stratified tank, PV + ESS
        composed variants. The default first stop.

    .. grid-item-card:: Ground-source heat pump boiler
        :link: gshpb
        :link-type: doc

        GSHPB core with g-function borehole, plus the same three
        composed variants as ASHPB.

    .. grid-item-card:: Water-source heat pump boiler
        :link: wshpb
        :link-type: doc

        WSHPB with a prescribed water-loop inlet temperature.

    .. grid-item-card:: Air-source heat pump (space conditioning)
        :link: ashp
        :link-type: doc

        ASHP for building heating / cooling load instead of DHW.

    .. grid-item-card:: Ground-source heat pump (space conditioning)
        :link: gshp
        :link-type: doc

        GSHP for building heating / cooling load instead of DHW.

.. toctree::
    :maxdepth: 1
    :hidden:

    ashpb
    gshpb
    wshpb
    ashp
    gshp
```

- [ ] **Step 3: Stage the new file**

```bash
git add docs/source/models/index.rst
```

### Task 1.2: Scaffold each model page with the fixed template

**Files:**
- Create: `docs/source/models/ashpb.rst`
- Create: `docs/source/models/gshpb.rst`
- Create: `docs/source/models/wshpb.rst`
- Create: `docs/source/models/ashp.rst`
- Create: `docs/source/models/gshp.rst`

- [ ] **Step 1: Write `models/ashpb.rst` skeleton**

Create `docs/source/models/ashpb.rst`:

```rst
====================================
Air-source heat pump boiler (ASHPB)
====================================

The ``ASHPB`` family pairs the shared refrigerant cycle with an
outdoor-coil source side and a DHW tank sink side. This is the
default first stop and the model the Getting Started flow uses.

Overview
========

ASHPB solves the closed refrigerant cycle every step against an
outdoor coil (ε-NTU air-side) and a tank-coupled condenser. The
class is :class:`tmhp.AirSourceHeatPumpBoiler`. Three composed
variants extend it with subsystems:

- :class:`tmhp.ASHPB_STC_preheat` — STC heats the mains feed before it
  reaches the tank.
- :class:`tmhp.ASHPB_STC_tank` — STC charges a separate top node of a
  stratified tank.
- :class:`tmhp.ASHPB_PV_ESS` — PV generation + battery storage routes
  electricity to the compressor and auxiliaries before drawing grid.

Base usage
==========

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   ashpb = AirSourceHeatPumpBoiler(ref="R32")

   # Steady-state snapshot
   result = ashpb.analyze_steady(
       T_tank_w=55.0,    # tank water [°C]
       T0=7.0,           # outdoor air [°C]
       Q_ref_cond=8_000, # target condenser duty [W]
   )

   # Time-stepping dynamic run — see Getting Started for full schedule
   # construction.
   # df = ashpb.analyze_dynamic(...)

Source-side mechanics
=====================

ASHPB models its outdoor coil as a variable-speed fan coupled to an
ε-NTU heat exchanger. Fan electrical power follows an ASHRAE
90.1-style cubic-with-speed curve; coil ε is recomputed each step
from the resolved refrigerant mass flow.

Sink-side mechanics
===================

The sink is a single-node DHW tank (with optional stratification on
the ``_STC_tank`` variant). The tank energy balance is solved
implicitly per step with ``fsolve``, jointly with the refrigerant
cycle.

Composed variants
=================

STC preheat
-----------

.. autoclass:: tmhp.ASHPB_STC_preheat
    :members:
    :show-inheritance:

STC with stratified tank
------------------------

.. autoclass:: tmhp.ASHPB_STC_tank
    :members:
    :show-inheritance:

PV + ESS
--------

.. autoclass:: tmhp.ASHPB_PV_ESS
    :members:
    :show-inheritance:

API reference
=============

.. automodule:: tmhp.air_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_stc_preheat
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_stc_tank
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_pv_ess
    :members:
    :undoc-members:
    :show-inheritance:

Validation
==========

ASHPB has been benchmarked against the Samsung EHS Mono HT Quiet R32
14 kW catalogue across 15 operating points. See
:doc:`../validation/index` for the parity plot and per-point
comparison.
```

- [ ] **Step 2: Write `models/gshpb.rst` skeleton**

Create `docs/source/models/gshpb.rst`:

```rst
======================================
Ground-source heat pump boiler (GSHPB)
======================================

The ``GSHPB`` family pairs the shared refrigerant cycle with a
ground-loop source side (vertical borehole field) and the same DHW
tank sink as ASHPB.

Overview
========

GSHPB solves the closed refrigerant cycle against a borehole heat
exchanger characterised by a precomputed **g-function**. The class is
:class:`tmhp.GroundSourceHeatPumpBoiler`. Three composed variants
extend it the same way ASHPB's do:

- :class:`tmhp.GSHPB_STC_preheat`
- :class:`tmhp.GSHPB_STC_tank`
- :class:`tmhp.GSHPB_PV_ESS`

Base usage
==========

.. code-block:: python

   from tmhp import GroundSourceHeatPumpBoiler

   gshpb = GroundSourceHeatPumpBoiler(
       ref="R410A",
       N_1=1, N_2=1,      # single borehole
       H_b=150.0,         # depth [m]
   )

   result = gshpb.analyze_steady(
       T_tank_w=55.0,
       T_source=10.0,     # ground-loop fluid inlet [°C]
       Q_ref_cond=8_000,
   )

Source-side mechanics
=====================

For ground-source models, the source-side dynamics are encoded in a
**g-function** — the dimensionless thermal response of a borehole
field to a unit heat-extraction step. ``tmhp`` precomputes the
g-function once via
`pygfunction <https://github.com/MassimoCimmino/pygfunction>`_ and
interpolates it during the simulation, so the per-step cost stays
constant whether the field is one borehole or a hundred.

.. figure:: ../_static/g_function_curve.svg
    :alt: g-function vs ln(t/t_s) for three rectangular borehole
        field geometries: 1×1, 2×2, and 4×4.
    :align: center
    :width: 100%

    Dimensionless g-function for three rectangular borehole-field
    geometries. The 1 × 1 field is the single-borehole baseline;
    2 × 2 and 4 × 4 diverge as borehole-to-borehole thermal
    interference accumulates over the multi-year horizon. Generated
    by ``scripts/visualization/g_function_curve.py``.

Sink-side mechanics
===================

Same as ASHPB — single-node DHW tank, implicit per-step solve.

Composed variants
=================

STC preheat
-----------

.. autoclass:: tmhp.GSHPB_STC_preheat
    :members:
    :show-inheritance:

STC with stratified tank
------------------------

.. autoclass:: tmhp.GSHPB_STC_tank
    :members:
    :show-inheritance:

PV + ESS
--------

.. autoclass:: tmhp.GSHPB_PV_ESS
    :members:
    :show-inheritance:

API reference
=============

.. automodule:: tmhp.ground_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_preheat
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_tank
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_pv_ess
    :members:
    :undoc-members:
    :show-inheritance:
```

- [ ] **Step 3: Write `models/wshpb.rst` skeleton**

Create `docs/source/models/wshpb.rst`:

```rst
=====================================
Water-source heat pump boiler (WSHPB)
=====================================

Source side is a water loop with a prescribed inlet temperature;
sink side is the same DHW tank used by ASHPB / GSHPB.

Overview
========

The class is :class:`tmhp.WaterSourceHeatPumpBoiler`. Unlike GSHPB,
WSHPB takes the source-side inlet temperature as a schedule input
rather than computing it from a borehole field — useful when the
water loop is driven by an external simulation or measurement.

Base usage
==========

.. code-block:: python

   from tmhp import WaterSourceHeatPumpBoiler

   wshpb = WaterSourceHeatPumpBoiler(ref="R134a")

   result = wshpb.analyze_steady(
       T_tank_w=55.0,
       T_source=15.0,     # water-loop inlet [°C]
       Q_ref_cond=8_000,
   )

Source-side mechanics
=====================

A single ε-NTU heat exchanger between the refrigerant evaporator
and the source-side water loop. No borehole transient — the loop
inlet temperature is whatever the user supplies.

Sink-side mechanics
===================

Same DHW tank as ASHPB / GSHPB.

API reference
=============

.. automodule:: tmhp.water_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:
```

- [ ] **Step 4: Write `models/ashp.rst` skeleton**

Create `docs/source/models/ashp.rst`:

```rst
================================================
Air-source heat pump (ASHP — space conditioning)
================================================

ASHP conditions a building zone (heating + cooling) rather than
charging a DHW tank. The refrigerant cycle and outdoor-coil source
side are shared with ASHPB; what differs is the load side — a zone
energy balance instead of a tank.

Overview
========

The class is :class:`tmhp.AirSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

Base usage
==========

.. code-block:: python

   from tmhp import AirSourceHeatPump

   ashp = AirSourceHeatPump(ref="R32")

   # See API reference below for the full constructor and
   # analyze_steady / analyze_dynamic signatures.

Source-side mechanics
=====================

Identical to ASHPB — outdoor coil with variable-speed fan, ε-NTU
air-side heat exchanger.

Sink-side mechanics
===================

A zone temperature / load proxy stands in for the building. The
heat pump's condenser duty serves whatever space-heating or
cooling load the caller supplies; there is no tank energy balance.

API reference
=============

.. automodule:: tmhp.air_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
```

- [ ] **Step 5: Write `models/gshp.rst` skeleton**

Create `docs/source/models/gshp.rst`:

```rst
==================================================
Ground-source heat pump (GSHP — space conditioning)
==================================================

GSHP conditions a building zone, drawing or rejecting heat through
the same g-function borehole heat exchanger as GSHPB.

Overview
========

The class is :class:`tmhp.GroundSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

Base usage
==========

.. code-block:: python

   from tmhp import GroundSourceHeatPump

   gshp = GroundSourceHeatPump(
       ref="R410A",
       N_1=1, N_2=1,
       H_b=150.0,
   )

   # See API reference below for the full constructor and
   # analyze_steady / analyze_dynamic signatures.

Source-side mechanics
=====================

Same g-function-based borehole as :doc:`gshpb`. See that page for the
detailed mechanic and the g-function figure.

Sink-side mechanics
===================

A zone temperature / load proxy stands in for the building, as in
:doc:`ashp`.

API reference
=============

.. automodule:: tmhp.ground_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
```

- [ ] **Step 6: Stage all five files**

```bash
git add docs/source/models/ashpb.rst docs/source/models/gshpb.rst docs/source/models/wshpb.rst docs/source/models/ashp.rst docs/source/models/gshp.rst
```

### Task 1.3: Wire Models into the site nav

**Files:**
- Modify: `docs/source/index.rst` (root toctree + landing-grid card)
- Modify: `docs/source/conf.py` (`nav_links` array)

- [ ] **Step 1: Read current `index.rst` toctree block**

```bash
grep -n "toctree\|getting-started/index\|concepts/index\|tutorials/index\|api/index\|validation/index" docs/source/index.rst
```

Expected output names the lines wrapping the `Documentation` toctree.

- [ ] **Step 2: Add Models toctree entry**

In `docs/source/index.rst`, find:

```rst
.. toctree::
   :maxdepth: 2
   :caption: Documentation
   :hidden:

   getting-started/index
   concepts/index
   tutorials/index
   api/index
   validation/index
```

Replace with:

```rst
.. toctree::
   :maxdepth: 2
   :caption: Documentation
   :hidden:

   getting-started/index
   concepts/index
   models/index
   tutorials/index
   api/index
   validation/index
```

- [ ] **Step 3: Add Models grid card on the landing page**

In `docs/source/index.rst`, find the `landing-cards` grid (look for `grid-item-card:: Concepts`). Insert a new card *between* `Concepts` and `Tutorials`:

```rst
    .. grid-item-card:: Models
        :link: models/index
        :link-type: doc

        ASHPB / GSHPB / WSHPB plus the space-conditioning ASHP /
        GSHP — each one a 1-stop page with source-side mechanics,
        composed subsystem variants, and API reference.

```

- [ ] **Step 4: Update `nav_links` in `conf.py`**

In `docs/source/conf.py`, find:

```python
    "nav_links": [
        {"title": "Getting Started", "url": "getting-started/index"},
        {"title": "Concepts", "url": "concepts/index"},
        {"title": "Tutorials", "url": "tutorials/index"},
        {"title": "API Reference", "url": "api/index"},
        {"title": "Validation", "url": "validation/index"},
    ],
```

Replace with:

```python
    "nav_links": [
        {"title": "Getting Started", "url": "getting-started/index"},
        {"title": "Concepts", "url": "concepts/index"},
        {"title": "Models", "url": "models/index"},
        {"title": "Tutorials", "url": "tutorials/index"},
        {"title": "API Reference", "url": "api/index"},
        {"title": "Validation", "url": "validation/index"},
    ],
```

- [ ] **Step 5: Verify Sphinx build is clean**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

- [ ] **Step 6: Commit Phase 1**

```bash
git add docs/source/index.rst docs/source/conf.py docs/source/models/
git commit -m "$(cat <<'EOF'
docs(ia): scaffold top-level Models section

Adds docs/source/models/ with five skeleton pages (ashpb, gshpb,
wshpb, ashp, gshp) following the spec's fixed template. Each page
already pulls in autoclass / automodule blocks for its target
classes, so the Models nav entry is functional from day one —
subsequent phases just thicken the prose and migrate content from
api/models, tutorials/compose-subsystems, and
concepts/cycle-architecture.

Nav wired up: Models added to conf.py nav_links between Concepts
and Tutorials, plus a landing-page grid card in the same position.
EOF
)"
```

---

## Phase 2 — Drop `api/models/`, redirect cross-references

The skeleton pages already contain the same autodoc blocks the
old `api/models/*` pages had, so this phase is mostly deletion +
fixing references.

### Task 2.1: Find all cross-references to `api/models/*`

**Files:**
- Search: entire `docs/source/`

- [ ] **Step 1: Locate every reference to `api/models/`**

```bash
grep -rn "api/models\|api.models" docs/source/ | grep -v "^Binary"
```

Save the output — every match needs to be redirected.

- [ ] **Step 2: Locate `:doc:` references that name model pages by path**

```bash
grep -rn ":doc:\`.*api/models" docs/source/
```

- [ ] **Step 3: Locate `:class:` and `:func:` references**

```bash
grep -rn ":class:\`tmhp\." docs/source/ | head -20
```

These should keep working unchanged (autodoc indexes classes by
fully qualified name, not by file location). Confirm a couple of
samples still resolve after Phase 2 by checking the build output.

### Task 2.2: Update every `api/models/*` `:doc:` reference

**Files:**
- Modify: `docs/source/api/index.rst` (drop Models section entirely)
- Modify: any other `.rst` file naming `api/models/...` paths

- [ ] **Step 1: Strip the Models section from `api/index.rst`**

In `docs/source/api/index.rst`, delete the entire "Models" `grid` block (between `Models\n======` and the next `Support modules\n===============` heading). Also remove `models/index` from the bottom `.. toctree::` directive.

After edit, `api/index.rst` should start with the original preamble paragraph, then jump directly to `Support modules`.

- [ ] **Step 2: Redirect any other `api/models/*` doc references**

For each match from Task 2.1 outside `api/index.rst`, edit the
file and replace `api/models/<page>` with `models/<page>` (paths
become absolute from `docs/source/`, so a typical
`:doc:`../api/models/ashpb`` from `docs/source/concepts/*.rst`
becomes `:doc:`../models/ashpb``).

Common candidates:
- `docs/source/concepts/cycle-architecture.rst`
- `docs/source/tutorials/compose-subsystems.rst` (this file will
  itself be deleted in Phase 3, so updating it is optional — but
  doing it now keeps each phase build-clean independently)
- `docs/source/index.rst`

- [ ] **Step 3: Verify build is clean (no broken cross-references)**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

If the build complains about unknown documents, the missing
target is in the warning text — fix it inline and rebuild.

### Task 2.3: Delete `api/models/`

**Files:**
- Delete: entire `docs/source/api/models/` directory

- [ ] **Step 1: Verify Phase 1 skeleton pages cover everything `api/models/` did**

```bash
diff -r docs/source/api/models docs/source/models 2>&1 | head -30
```

You expect file-level differences (the new pages have more prose);
what matters is that every `automodule` block in `api/models/*`
appears in the corresponding `models/*` page. Spot-check by
opening both files side by side.

- [ ] **Step 2: Remove the directory with `git rm`**

```bash
git rm -r docs/source/api/models/
```

This preserves history for `git log --follow`.

- [ ] **Step 3: Verify build is clean**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

- [ ] **Step 4: Commit Phase 2**

```bash
git add docs/source/api/index.rst docs/source/api/models/
git commit -m "$(cat <<'EOF'
docs(ia): remove api/models/, redirect references to /models/

Models reference moves to the new top-level Models section
introduced in Phase 1. Every `:doc:` reference that pointed at
`api/models/<page>` now points at `models/<page>`. `api/index.rst`
loses its Models grid + the models/index toctree entry; the page
now opens directly with Support modules.

Class-level references (`:class:`tmhp.ASHPB_PV_ESS``) are
unaffected — Sphinx indexes them by fully qualified name, not by
file location.
EOF
)"
```

---

## Phase 3 — Absorb `tutorials/compose-subsystems` into Models pages

### Task 3.1: Read the source and identify what goes where

**Files:**
- Read: `docs/source/tutorials/compose-subsystems.rst`

- [ ] **Step 1: Capture the file contents for migration**

```bash
cat docs/source/tutorials/compose-subsystems.rst
```

The file has these blocks (the migration map maps each to its
destination):

- "The two pieces" → drop (already covered in Models pages)
- "Putting them together" (STC instantiation + ASHPB_STC_preheat
  constructor call) → `models/ashpb.rst` § Composed variants > STC
  preheat (new sub-block before the autoclass)
- "Driving it with irradiance" (`I_DN_schedule` / `I_dH_schedule`
  driving code) → `models/ashpb.rst` § Composed variants > STC
  preheat (same block as above, after the constructor)
- "Reading the contribution" (base vs STC comparison code) →
  `models/ashpb.rst` § Composed variants > STC preheat (final
  sub-block)
- "Other compositions" list → drop (the Models page already
  inventories its variants)
- "A look at the PV + ESS ledger" + the PV/ESS figure → already
  present in current `compose-subsystems.rst` from the prior
  figure session → `models/ashpb.rst` § Composed variants > PV + ESS

### Task 3.2: Insert the STC preheat usage block into `models/ashpb.rst`

**Files:**
- Modify: `docs/source/models/ashpb.rst`

- [ ] **Step 1: Find the existing STC preheat sub-section**

In `docs/source/models/ashpb.rst`, locate:

```rst
STC preheat
-----------

.. autoclass:: tmhp.ASHPB_STC_preheat
    :members:
    :show-inheritance:
```

- [ ] **Step 2: Insert the usage example before the autoclass**

Replace the block above with:

```rst
STC preheat
-----------

A :class:`~tmhp.subsystems.SolarThermalCollector` heats the cold mains
water before it reaches the DHW tank, so the heat pump sees pre-heated
water during the preheat window.

.. code-block:: python

   import numpy as np

   from tmhp import ASHPB_STC_preheat
   from tmhp.subsystems import SolarThermalCollector

   stc = SolarThermalCollector(
       A_stc=4.0,             # 4 m² collector area
       stc_tilt=35.0,
       stc_azimuth=180.0,
   )

   model = ASHPB_STC_preheat(stc=stc, ref="R32")

Drive it the same way as base ASHPB, adding irradiance schedules
(``I_DN_schedule``, ``I_dH_schedule``) — both in W/m² per step:

.. code-block:: python

   dt_s          = 60
   n_steps       = 24 * 3600 // dt_s
   hour_of_day   = np.arange(n_steps) / 60.0

   # Crude clear-sky irradiance: bell from 06:00 to 18:00.
   day_window    = (hour_of_day >= 6.0) & (hour_of_day <= 18.0)
   sun_shape     = np.sin(np.pi * (hour_of_day - 6.0) / 12.0)
   I_DN          = np.where(day_window, 800.0 * sun_shape, 0.0)
   I_dH          = np.where(day_window, 100.0 * sun_shape, 0.0)

   T0  = np.full(n_steps, 5.0)
   dhw = np.zeros(n_steps)

   df = model.analyze_dynamic(
       simulation_period_sec = n_steps * dt_s,
       dt_s                  = dt_s,
       T_tank_w_init_C       = 50.0,
       dhw_usage_schedule    = dhw,
       T0_schedule           = T0,
       I_DN_schedule         = I_DN,
       I_dH_schedule         = I_dH,
   )

To quantify the contribution, run the same schedules through a base
``AirSourceHeatPumpBoiler`` and difference the daily compressor
energy:

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   base = AirSourceHeatPumpBoiler(ref="R32").analyze_dynamic(
       simulation_period_sec = n_steps * dt_s,
       dt_s                  = dt_s,
       T_tank_w_init_C       = 50.0,
       dhw_usage_schedule    = dhw,
       T0_schedule           = T0,
   )

   def daily_kwh(s, dt_s=dt_s):
       return float(s.sum()) * dt_s / 3.6e6

   saving = daily_kwh(base["E_cmp [W]"]) - daily_kwh(df["E_cmp [W]"])
   print(f"STC preheat saving: {saving:.2f} kWh/day")

.. autoclass:: tmhp.ASHPB_STC_preheat
    :members:
    :show-inheritance:
```

### Task 3.3: Insert the PV+ESS energy-balance figure into `models/ashpb.rst`

**Files:**
- Modify: `docs/source/models/ashpb.rst`

- [ ] **Step 1: Find the PV+ESS sub-section**

In `docs/source/models/ashpb.rst`, locate:

```rst
PV + ESS
--------

.. autoclass:: tmhp.ASHPB_PV_ESS
    :members:
    :show-inheritance:
```

- [ ] **Step 2: Insert the figure block and a brief intro**

Replace the block above with:

```rst
PV + ESS
--------

:class:`~tmhp.subsystems.PhotovoltaicSystem` generation feeds the
compressor and auxiliaries; an :class:`~tmhp.subsystems.EnergyStorageSystem`
buffers midday surplus for evening load; grid import covers
whatever the two cannot supply.

.. code-block:: python

   from tmhp import ASHPB_PV_ESS
   from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

   model = ASHPB_PV_ESS(
       pv  = PhotovoltaicSystem(),
       ess = EnergyStorageSystem(),
       ref = "R32",
   )

   # Pass I_DN_schedule + I_dH_schedule to analyze_dynamic exactly
   # as you would for ASHPB_STC_preheat.

.. figure:: ../_static/pv_ess_energy_balance.svg
    :alt: Two-panel daily energy balance for the PV + ESS scenario.
        Panel (a) is the timeseries of PV generation, HP load, and
        grid import. Panel (b) is the stacked-bar daily ledger of
        where PV ended up and where HP load came from.
    :align: center
    :width: 100%

    24-hour ``ASHPB_PV_ESS`` run with a clear-sky irradiance profile
    and default ``PhotovoltaicSystem`` / ``EnergyStorageSystem`` sizes.
    Panel (b) makes the sizing tradeoff readable: shrinking the ESS
    column on the left would push more of the right-hand bar from
    "PV" to "Grid". Generated by
    ``scripts/visualization/pv_ess_energy_balance.py``.

.. autoclass:: tmhp.ASHPB_PV_ESS
    :members:
    :show-inheritance:
```

### Task 3.4: Delete `tutorials/compose-subsystems.rst` and dewire it

**Files:**
- Delete: `docs/source/tutorials/compose-subsystems.rst`
- Modify: `docs/source/tutorials/index.rst`

- [ ] **Step 1: Remove the grid card and toctree entry from `tutorials/index.rst`**

In `docs/source/tutorials/index.rst`, find:

```rst
    .. grid-item-card:: Compose subsystems
        :link: compose-subsystems
        :link-type: doc

        Wire a ``SolarThermalCollector`` onto ``ASHPB_STC_preheat`` and
        feed irradiance schedules into ``analyze_dynamic``.
```

Delete the whole `grid-item-card:: Compose subsystems` block.

In the same file, find the bottom toctree:

```rst
    swap-refrigerant
    realistic-dynamic-simulation
    compose-subsystems
    visualize-the-cycle
```

Remove the `compose-subsystems` line.

- [ ] **Step 2: Delete the source file**

```bash
git rm docs/source/tutorials/compose-subsystems.rst
```

- [ ] **Step 3: Verify build is clean**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

- [ ] **Step 4: Commit Phase 3**

```bash
git add docs/source/models/ashpb.rst docs/source/tutorials/index.rst docs/source/tutorials/compose-subsystems.rst
git commit -m "$(cat <<'EOF'
docs(ia): fold compose-subsystems into Models > ASHPB

Tutorials/compose-subsystems was a hybrid: it explained the
ASHPB_STC_preheat usage pattern and pointed forward at PV+ESS.
That belongs next to the autoclass in models/ashpb under "Composed
variants" — moving it makes ASHPB a true 1-stop page.

- STC preheat sub-section gets the irradiance-schedule example and
  the base-vs-STC contribution comparison.
- PV + ESS sub-section gets the daily energy-balance figure that
  already lived under "compose-subsystems".
- tutorials/compose-subsystems.rst removed; tutorials/index grid
  + toctree updated.
EOF
)"
```

---

## Phase 4 — Slim `concepts/cycle-architecture`

### Task 4.1: Identify the system-specific blocks to remove

**Files:**
- Read: `docs/source/concepts/cycle-architecture.rst`

- [ ] **Step 1: Capture the file**

```bash
grep -n "^=\|^-\|^Source side\|^Sink side\|^The borehole g-function\|^Source families\|^Sink families" docs/source/concepts/cycle-architecture.rst
```

Look for these blocks to remove:

- "The borehole g-function" sub-section + the figure embed
- The per-source detail list-table (the one that maps each source
  family to its API page — the Models nav already does that job)

Look for these to keep:

- The Cytoscape interactive cycle diagram + its `raw:: html` block
- The "Composed subsystems" overview paragraph
- The shared closed-cycle prose

### Task 4.2: Remove the system-specific blocks

**Files:**
- Modify: `docs/source/concepts/cycle-architecture.rst`

- [ ] **Step 1: Delete "The borehole g-function" sub-section**

In `docs/source/concepts/cycle-architecture.rst`, find the heading:

```rst
The borehole g-function
-----------------------
```

Delete from that heading through (but not including) the next
peer heading. This removes both the figure embed and the
explanatory prose. The g-function figure now lives only in
`models/gshpb.rst` (added in Phase 1).

- [ ] **Step 2: Delete the per-source list-table**

In `docs/source/concepts/cycle-architecture.rst`, find the
`Source side: where heat comes from` (or similarly named) section
and the matching `Sink side: where heat goes` section. Each
contains a `list-table` mapping families to API doc paths.

Replace both list-tables with a short paragraph that points the
reader at the Models section:

```rst
Per-source mechanics — the outdoor coil for ASHP, the g-function
borehole for GSHP, the prescribed water inlet for WSHP — live on
each model's page under :doc:`../models/index`. The sink side
(DHW tank or building load) is documented the same way.
```

- [ ] **Step 3: Verify the build is clean**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

- [ ] **Step 4: Commit Phase 4**

```bash
git add docs/source/concepts/cycle-architecture.rst
git commit -m "$(cat <<'EOF'
docs(ia): slim cycle-architecture to the shared abstraction

The "Source side" / "Sink side" list-tables and the borehole
g-function sub-section were system-specific content sitting on a
page whose job is the source-agnostic closed-cycle abstraction.
They moved to the corresponding Models pages in Phase 1
(g-function → models/gshpb; source/sink mechanics → each model's
own "Source-side mechanics" + "Sink-side mechanics" sections).

The page now points readers at Models for those details and keeps
only the shared closed-cycle prose plus the Cytoscape interactive
diagram.
EOF
)"
```

---

## Phase 5 — Polish: getting-started outflow + realistic-dynamic figure

### Task 5.1: Update `first-dynamic-simulation` next-moves links

**Files:**
- Modify: `docs/source/getting-started/first-dynamic-simulation.rst`

- [ ] **Step 1: Read the current `Common next moves` block**

```bash
grep -n -A 20 "^Common next moves" docs/source/getting-started/first-dynamic-simulation.rst
```

- [ ] **Step 2: Redirect the GSHP / subsystem cross-references**

In `docs/source/getting-started/first-dynamic-simulation.rst`, find
the bullet about composed subsystems / hybrid PV / STC variants.
Replace any `:doc:`../api/models/ashpb`` or `:doc:`../api/models/gshpb``
with `:doc:`../models/ashpb`` and `:doc:`../models/gshpb``
respectively.

If the bullet currently reads something like:

```rst
- For solar-coupled or PV/ESS variants, instantiate the corresponding
  subclass (``ASHPB_STC_preheat``, ``ASHPB_STC_tank``, ``ASHPB_PV_ESS``,
  or the GSHPB counterparts) and pass the additional schedules
  (``I_DN_schedule``, ``I_dH_schedule``, ``T_sup_w_schedule``) as
  documented under :doc:`../api/models/ashpb` and
  :doc:`../api/models/gshpb`.
```

Update the two doc paths to `../models/ashpb` and `../models/gshpb`.

### Task 5.2: Embed the 24h dynamic figure into `realistic-dynamic-simulation`

**Files:**
- Modify: `docs/source/tutorials/realistic-dynamic-simulation.rst`

- [ ] **Step 1: Confirm the figure file exists**

```bash
ls docs/source/_static/dynamic_24h_timeseries.svg
```

Expected: file present (from the prior figure session).

- [ ] **Step 2: Read the current file to find an insertion point**

```bash
head -60 docs/source/tutorials/realistic-dynamic-simulation.rst
```

Look for the first explanatory paragraph (after the title) — the
figure goes immediately after it as a "what a run looks like"
anchor for the rest of the tutorial.

- [ ] **Step 3: Insert the figure block**

After the first paragraph (typically ending with the page's
intent), add:

```rst
What a real day looks like
==========================

.. figure:: ../_static/dynamic_24h_timeseries.svg
    :alt: 24-hour dynamic simulation of the ASHPB. Three stacked
        panels — tank temperature with its upper/lower bounds,
        condenser heat rate and compressor electrical power, and
        instantaneous + running-mean system COP.
    :align: center
    :width: 100%

    24-hour ASHPB run with two DHW draws (07:00 and 20:00) and a
    sinusoidal outdoor temperature. Generated by
    ``scripts/visualization/dynamic_24h_timeseries.py``.

```

If the same figure was already embedded by an earlier session on
`getting-started/first-dynamic-simulation.rst`, **leave that
copy in place** — the same figure appearing on both pages is
fine; the tutorial provides the realistic-schedule context the
getting-started page does not.

- [ ] **Step 4: Verify the build is clean**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

- [ ] **Step 5: Commit Phase 5**

```bash
git add docs/source/getting-started/first-dynamic-simulation.rst docs/source/tutorials/realistic-dynamic-simulation.rst
git commit -m "$(cat <<'EOF'
docs(ia): redirect getting-started outflow, embed 24h figure in tutorial

- first-dynamic-simulation "Common next moves" now points
  readers at Models (the api/models/* paths it referenced no
  longer exist after Phase 2).
- realistic-dynamic-simulation embeds the 24h timeseries figure
  generated in the prior session, giving the tutorial a "what a
  real run looks like" anchor before the schedule construction
  prose.
EOF
)"
```

---

## Phase 6 — Linkcheck + final verification

### Task 6.1: Run a full linkcheck pass

- [ ] **Step 1: Run sphinx linkcheck**

```bash
uv run --group docs sphinx-build -W --keep-going -E -b linkcheck docs/source docs/build/linkcheck
```

Expected: every external link reports `ok`. Internal links are
also checked via the regular html build's `-W` flag.

If any external link fails, decide case-by-case: re-tries are
fine for transient 429s; replace dead links with current
equivalents.

### Task 6.2: Final fresh html build

- [ ] **Step 1: Wipe the build directory and rebuild**

```bash
rm -rf docs/build
uv run --group docs sphinx-build -W --keep-going -E -b html docs/source docs/build/html
```

Expected: `build succeeded.` with 0 warnings.

### Task 6.3: Visual spot-check of the new nav

- [ ] **Step 1: Open the rendered landing page**

```bash
open docs/build/html/index.html
```

Confirm by eye:

- Top nav shows six links in order: Getting Started, Concepts,
  Models, Tutorials, API Reference, Validation.
- Landing-page grid card "Models" appears between Concepts and
  Tutorials, links to `models/index.html`.
- The Models page lists all five model cards (ASHPB / GSHPB /
  WSHPB / ASHP / GSHP).
- Each model page renders its template sections (Overview / Base
  usage / Source-side / Sink-side / Composed variants where
  applicable / API reference / Validation where applicable).
- The g-function figure appears on `models/gshpb.html`, not on
  `concepts/cycle-architecture.html`.
- The PV+ESS figure appears on `models/ashpb.html`, not on a
  tutorials page.
- `tutorials/compose-subsystems.html` returns a 404 (or is
  absent from the rendered tree).
- `api/models/ashpb.html` returns a 404 (or is absent).

- [ ] **Step 2: Commit any cosmetic fixes you found, then push**

If the spot-check surfaced anything, fix and commit those
adjustments. Otherwise:

```bash
git push
```

This is the only `git push` in the plan — phases 1-5 commit
locally so a regression in a later phase is easy to isolate
with `git reset`.

---

## Out of scope (per spec)

- Re-authoring figure scripts (the seven figures from the prior
  session stay as they are).
- Translating new prose into Korean.
- Adding redirect stubs at old URLs — sphinx-notfound-page
  already serves a global 404; the spec's "decision deferred"
  note stays deferred.

## Validation summary

Every phase ends at a clean `sphinx-build -W` state. The
verification chain across phases is:

| Phase | Verifier |
|---|---|
| 1 | `sphinx-build -W -E -b html` clean |
| 2 | same — confirms no broken `:doc:` references after move |
| 3 | same — confirms compose-subsystems removal didn't leave dangling refs |
| 4 | same — confirms cycle-architecture still parses after slimming |
| 5 | same — confirms next-moves links resolve under new paths |
| 6 | full linkcheck + fresh-build html clean |
