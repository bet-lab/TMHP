=========================
Refrigerants and CoolProp
=========================

"Refrigerant-agnostic" is one of the headline properties of
TMHP — and this is the page that makes it concrete. The
refrigerant is a constructor argument on every released
cycle-resolved model family (``ref="<name>"``); state queries route through
`CoolProp <http://www.coolprop.org>`_, which carries the
equation-of-state heavy lifting. Below: which refrigerants work
out of the box, where the cycle assumptions break, and how to read
CoolProp-related failures.

The contract
============

.. raw:: html

   <div id="ph-chart-mount"
        data-refrigerants="R32,R290,R134a,R1234yf"
        data-default="R32"></div>
   <script src="../_static/js/lib/d3.v7.custom.min.js"></script>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/plots/ph-chart.js"></script>

Any refrigerant CoolProp supports as a pure fluid or a mixture
that CoolProp can resolve via its built-in alias table is usable
out of the box:

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   AirSourceHeatPumpBoiler(ref="R32")        # difluoromethane
   AirSourceHeatPumpBoiler(ref="R290")       # propane
   AirSourceHeatPumpBoiler(ref="R410A")      # mixture
   AirSourceHeatPumpBoiler(ref="R134a")
   AirSourceHeatPumpBoiler(ref="R744")       # CO₂ (see below)
   AirSourceHeatPumpBoiler(ref="R600a")      # isobutane

CoolProp returns REFPROP-grade equation-of-state values, so the same
first-principles cycle calculation can be rerun with a different
working fluid without per-refrigerant curve refitting.

Subcritical operation (R32, R290, R410A, R134a, …)
==================================================

The cycle assumes a subcritical condensation pressure: condenser
inlet is superheated vapour, condenser outlet is subcooled
liquid, and the saturation dome is the relevant region for
state-point calculations.

For refrigerants whose critical temperature is comfortably above
typical condenser water temperatures (R32: ~78 °C critical, R290:
~97 °C, R134a: ~101 °C), this assumption holds across the entire
DHW operating envelope.

Supercritical / transcritical operation (R744)
==============================================

R744 (CO₂) has a critical temperature near 31 °C. For DHW heating
(condenser water at 50–65 °C) the high side runs *above* the
critical point — a transcritical cycle. The current model is
written against a subcritical-condenser assumption, so R744 will
still solve but the results are best interpreted as a
sanity-check, not a fully transcritical model.

If you need a faithful transcritical model, a future cycle path
that treats the gas-cooler explicitly is the right place to add
it. The cycle-closure interface (``_optimize_operation``) is
designed so that swapping the high-side block in is a localised
change.

Mapping CoolProp errors to ``failure_reason``
==============================================

CoolProp raises when state queries fall outside the EOS
envelope — for example, asking for a saturation pressure above
the critical point, or asking for a property at a state that
crossed into two-phase by accident. These show up inside
TMHP as:

- ``failure_reason == "cycle_invalid"`` when the cycle couldn't
  produce a coherent state at all (often the EOS itself rejected
  a query).
- ``failure_reason == "hx_not_converged"`` when the cycle
  produced a state but the HX iteration drifted because a
  property lookup was unstable near a phase boundary.

In both cases the relevant lever is the *operating point*:
either move ``T_tank_w`` / ``T0`` / ``Q_ref_tank`` away from
the EOS edge, or oversize the heat exchanger so the iteration
doesn't push so hard. See :doc:`failure-reason-semantics` for
the branching pattern.

Why CoolProp specifically?
==========================

- **Coverage** — every common refrigerant plus most blends, with
  the same call shape.
- **Quality** — REFPROP-grade EOS for the major fluids, validated
  against the same reference data that commercial property
  packages use.
- **License** — MIT, redistributable.
- **Cost** — pure-Python wrapper over a C++ core, fast enough
  that 100 k state queries per simulation second are not the
  bottleneck.

For state-point helpers and the small wrapper layer this library
adds, see :doc:`../api/support/refrigerant-thermo`.
