================================
Thermodynamic cycle architecture
================================

.. |epsilon-ntu| raw:: html

   <span class="glossary" data-term="epsilon-ntu">ε-NTU</span>

.. |cop| raw:: html

   <span class="glossary" data-term="cop">COP</span>

Every released cycle-resolved model family in TMHP wraps the same
closed refrigerant cycle with a different source / sink boundary. That
single piece of shared machinery is what lets one library cover ASHPB,
GSHPB, WSHPB, ASHP, GSHP, and subsystem variants on top — without
rewriting the thermodynamics each time. This page sketches the shared
structure and shows where each system family plugs into it.

The shared core
===============

Every cycle-resolved family reuses the same closed cycle — only the
blocks marked *source side* and *sink side* swap out per family. The
interactive diagram is drawn in heating / DHW orientation. For ASHP and
GSHP cooling, ``Q_r_iu > 0`` maps the indoor unit to the evaporator and
the environmental side to the condenser; output columns stay labelled by
physical location.

.. raw:: html

   <style>
     .cycle-arch-card {
       margin: 1.25em auto;
       border: 1px solid var(--sy-c-border, #e5e7eb);
       border-radius: 8px;
       background: var(--sy-c-bg, #fff);
       padding: 16px 18px;
       position: relative;
       max-width: 100%;
     }
     #cy-arch {
       width: 100%;
       height: 320px;
       border-radius: 6px;
     }
     .cycle-arch-toolbar {
       position: absolute; top: 22px; right: 22px;
       display: flex; gap: 6px;
       background: rgba(255,255,255,0.9);
       border: 1px solid #e5e7eb; border-radius: 8px;
       padding: 4px; z-index: 5;
     }
     .cycle-arch-toolbar button {
       background: transparent; border: 0;
       width: 28px; height: 28px;
       border-radius: 5px; cursor: pointer;
       color: #4b5563; font-size: 14px;
       font-family: inherit;
     }
     .cycle-arch-toolbar button:hover { background: #f3f4f6; color: #1f2937; }
     #cy-arch-info {
       margin-top: 12px;
       background: linear-gradient(180deg, #0f1622 0%, #131a26 100%);
       color: #e6ecf5;
       padding: 14px 16px; border-radius: 8px;
       font-size: 13px; line-height: 1.55;
       border: 1px solid #1f2a3a;
       min-height: 4em;
     }
     #cy-arch-info .placeholder { color: #6b7280; font-style: italic; }
     #cy-arch-info .id  { font-weight: 600; color: #c7d2fe; font-size: 14px; }
     #cy-arch-info .ty  { font-size: 11px; padding: 1px 7px; border-radius: 999px;
                          background: #1f2a3a; color: #9aa7b8; margin-left: 6px;
                          vertical-align: 1px; }
     #cy-arch-info code { background: rgba(255,255,255,0.07); padding: 1px 6px;
                          border-radius: 4px; font-size: 12px; color: #d1d5db; }
     #cy-arch-info .apilink {
       display: inline-block; margin-top: 8px;
       color: #8b9eff; text-decoration: none; font-size: 12px;
     }
     #cy-arch-info .apilink:hover { text-decoration: underline; }
     #cy-arch-info .body { margin-top: 6px; }
     .cycle-arch-hint { color: #6b7280; font-size: 12px; margin-top: 8px; }
     .cycle-arch-caption {
       text-align: center; font-style: italic;
       color: var(--sy-c-text-secondary, #6b7280);
       font-size: 0.92em; margin-top: 0.6em;
     }
   </style>

   <div class="cycle-arch-card">
     <div class="cycle-arch-toolbar">
       <button id="cy-arch-fit"      title="Fit to view">⤢</button>
       <button id="cy-arch-zoom-in"  title="Zoom in">+</button>
       <button id="cy-arch-zoom-out" title="Zoom out">−</button>
     </div>
     <div id="cy-arch"></div>
     <div id="cy-arch-info">
       <span class="placeholder">Click a node to see its code mapping and API link.</span>
     </div>
     <p class="cycle-arch-hint">Topology is fixed · scroll-wheel to zoom · drag empty space to pan</p>
   </div>

   <p class="cycle-arch-caption">
     Heating / DHW orientation of the data flow shared by cycle-resolved
     <strong>TMHP</strong> families. Bold blocks are reused across ASHPB,
     GSHPB, WSHPB, ASHP, and GSHP.
   </p>

   <script src="../_static/js/lib/cytoscape.min.js"></script>
   <script>
   (function () {
     if (!document.getElementById("cy-arch")) return;

     // ─── Data extracted from TMHP source ───────────────────────
     //   ASHPB._calc_state, refrigerant.calc_ref_state, heat_transfer.py,
     //   _opt_utils.py (scalar minimizer over dT_ref_ou).
     const nodes = [
       { id: "SRC",  type: "src",    title: "Source side",      sub: "air · ground · water",
         code: "air_source_heat_pump*.py / ground_source_heat_pump*.py / water_source_heat_pump_boiler.py — outdoor coil, borehole g-function, prescribed water inlet",
         api:  "../models/index.html" },
       { id: "EVAP", type: "cycle",  title: "Evaporator HX",    sub: "ε-NTU",
         code: "_calc_state: evaporating saturation temperature and ε-NTU heat transfer",
         api:  "../api/support/heat-transfer.html" },
       { id: "CMP",  type: "cycle",  title: "Compressor",       sub: "η_is · η_vol · η_mech",
         code: "_calc_state lines 410–471: h_cmp_out via η_isen, m_dot = V_disp·ρ·η_vol·rps",
         api:  "../models/ashpb.html" },
       { id: "COND", type: "cycle",  title: "Condenser HX",     sub: "ε-NTU",
         code: "_calc_state: condensing saturation temperature and ε-NTU heat transfer",
         api:  "../api/support/heat-transfer.html" },
       { id: "EXP",  type: "cycle",  title: "Expander",         sub: "isenthalpic",
         code: "refrigerant.py:calc_ref_state (h_exp_out = h_exp_in throttle)",
         api:  "../api/support/refrigerant-thermo.html" },
       { id: "SINK", type: "sink",   title: "Sink side",        sub: "DHW tank · building load",
         code: "dhw.py (tank energy balance) / air_source_heat_pump.py (building load)",
         api:  "../api/support/subsystems.html" },
       { id: "OPT",  type: "solver", title: "Cycle closure",    sub: "min electrical input",
         code: "_optimize_operation — choose heat-exchanger approach temperatures / speed for a feasible low-power operating point",
         api:  "cycle-architecture.html#the-shared-core" },
     ];
     const edges = [
       { source: "SRC",  target: "EVAP", label: "Q_evap" },
       { source: "EVAP", target: "CMP",  label: "low-P vapor" },
       { source: "CMP",  target: "COND", label: "high-P vapor" },
       { source: "COND", target: "EXP",  label: "liquid" },
       { source: "EXP",  target: "EVAP", label: "two-phase" },
       { source: "COND", target: "SINK", label: "demand duty" },
       { source: "OPT",  target: "EVAP", label: "optimizes", kind: "dashed" },
       { source: "OPT",  target: "CMP",  label: "optimizes", kind: "dashed" },
     ];

     const elements = [
       ...nodes.map(n => ({ data: { ...n, label: n.title + "\n" + n.sub } })),
       ...edges.map(e => ({ data: e })),
     ];

     const palette = {
       src:    { fill: "#fef3c7", border: "#d97706", text: "#78350f" },
       cycle:  { fill: "#eef2ff", border: "#6366f1", text: "#1e1b4b" },
       sink:   { fill: "#dbeafe", border: "#2563eb", text: "#1e3a8a" },
       solver: { fill: "#f5f3ff", border: "#8b5cf6", text: "#4c1d95" },
     };

     const cy = cytoscape({
       container: document.getElementById("cy-arch"),
       elements,
       style: [
         { selector: "node", style: {
             "label": "data(label)", "text-wrap": "wrap",
             "text-valign": "center", "text-halign": "center",
             "font-size": 12, "font-weight": 600,
             "font-family": "-apple-system, BlinkMacSystemFont, Inter, sans-serif",
             "width": "label", "height": "label",
             "padding": "14px", "shape": "round-rectangle",
             "corner-radius": "10", "border-width": 1.5,
             "line-height": 1.35,
         } },
         { selector: 'node[type = "src"]',    style: {
             "background-color": palette.src.fill,    "border-color": palette.src.border,    "color": palette.src.text } },
         { selector: 'node[type = "cycle"]',  style: {
             "background-color": palette.cycle.fill,  "border-color": palette.cycle.border,  "color": palette.cycle.text } },
         { selector: 'node[type = "sink"]',   style: {
             "background-color": palette.sink.fill,   "border-color": palette.sink.border,   "color": palette.sink.text } },
         { selector: 'node[type = "solver"]', style: {
             "background-color": palette.solver.fill, "border-color": palette.solver.border, "color": palette.solver.text,
             "border-style": "dashed", "corner-radius": "22" } },
         { selector: "edge", style: {
             "width": 1.5, "line-color": "#475569",
             "target-arrow-color": "#475569", "target-arrow-shape": "triangle",
             "arrow-scale": 1.1, "curve-style": "bezier",
             "label": "data(label)", "font-size": 10, "color": "#374151",
             "text-outline-color": "#fff", "text-outline-opacity": 1, "text-outline-width": 1.5,
         } },
         { selector: 'edge[kind = "dashed"]', style: {
             "line-style": "dashed", "line-color": "#8b5cf6",
             "target-arrow-color": "#8b5cf6", "color": "#4c1d95",
         } },
         { selector: "node:selected", style: { "border-width": 3 } },
         { selector: ".faded",        style: { "opacity": 0.25 } },
       ],
       layout: { name: "preset", positions: {
         SRC:  { x: 100, y: 220 }, EVAP: { x: 320, y: 220 },
         CMP:  { x: 540, y: 100 }, COND: { x: 760, y: 220 },
         EXP:  { x: 540, y: 340 }, SINK: { x: 960, y: 220 },
         OPT:  { x: 320, y:  40 },
       } },
       autoungrabify: true,
       minZoom: 0.4, maxZoom: 2.5,
       wheelSensitivity: 0.2,
     });

     const info = document.getElementById("cy-arch-info");
     const placeholder = '<span class="placeholder">Click a node to see its code mapping and API link.</span>';
     function showNode(n) {
       info.innerHTML =
         '<span class="id">' + n.title + '</span><span class="ty">' + n.type + '</span>' +
         '<div class="body"><code>' + n.code + '</code></div>' +
         '<a class="apilink" href="' + n.api + '">📖 Open API docs →</a>';
     }
     cy.on("tap", "node", evt => {
       showNode(evt.target.data());
       cy.elements().addClass("faded");
       evt.target.closedNeighborhood().removeClass("faded");
     });
     cy.on("tap", evt => {
       if (evt.target === cy) {
         info.innerHTML = placeholder;
         cy.elements().removeClass("faded");
       }
     });

     document.getElementById("cy-arch-fit").addEventListener("click",
       () => cy.fit(undefined, 40));
     document.getElementById("cy-arch-zoom-in").addEventListener("click",
       () => cy.zoom({ level: cy.zoom() * 1.25,
                       renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }));
     document.getElementById("cy-arch-zoom-out").addEventListener("click",
       () => cy.zoom({ level: cy.zoom() * 0.8,
                       renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }));

     document.fonts && document.fonts.ready
       && document.fonts.ready.then(() => cy.fit(undefined, 40));
     setTimeout(() => cy.fit(undefined, 40), 0);
   })();
   </script>

.. figure:: ../_static/source_sink_matrix.svg
    :alt: TMHP released source and sink matrix showing DHW boiler
        families for air, ground, and water and space-conditioning
        families for air and ground.
    :align: center
    :width: 90%

    Model-family view of TMHP. The refrigerant-cycle core stays fixed;
    released model families swap the environmental medium, demand
    boundary, and optional subsystems around that core.

The cycle solves four refrigerant state points (compressor in /
out, expander in / out) plus the evaporator and condenser
saturation states. Heat transfer at each heat exchanger is solved
with an |epsilon-ntu| model. The evaporating temperature is left as a
free parameter and chosen by minimizing compressor power, so the
cycle closes on a physical optimum rather than on a fitted
coefficient.

Per-source mechanics — the outdoor coil for ASHP/ASHPB, the g-function
borehole for GSHP/GSHPB, and the prescribed water inlet for WSHPB —
live on each model's page under :doc:`../models/index`. The released
demand boundary (DHW tank or building load) is documented the same way.

Composed subsystems
===================

The ``*_stc_*`` and ``*_pv_ess`` variants reuse the same core cycle
and add one or more subsystems on the demand side:

- **Solar thermal collector (STC) preheat** — STC heats the mains
  water before it reaches the tank. Reduces the tank-charge duty
  the heat pump has to deliver.
- **STC with stratified tank** — STC charges a separate top node
  of a stratified tank; the heat pump charges the bottom.
- **PV + ESS** — photovoltaic generation feeds an energy storage
  system that supplies the compressor and auxiliary loads
  preferentially.

These are documented under :doc:`../api/support/subsystems`.

Why the structure matters
=========================

Because the refrigerant-cycle core is reused across the cycle-resolved
families, a parameter sweep across refrigerants, source types, or
subsystem combinations doesn't require re-implementing the
thermodynamics — it requires picking the appropriate released class and
schedule. The cycle-level invariants (energy balance, |cop| definitions,
:doc:`failure_reason semantics <failure-reason-semantics>`) therefore
hold identically across the family. Results from ASHPB, GSHPB, WSHPB,
ASHP, and GSHP remain comparable when the operating point and
source/sink boundary are matched.
