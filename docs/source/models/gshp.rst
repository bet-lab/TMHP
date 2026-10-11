===================================================
Ground-source heat pump (GSHP — space conditioning)
===================================================

.. |gshp| raw:: html

   <span class="glossary" data-term="gshp">GSHP</span>

|gshp| conditions a building zone, drawing or rejecting heat through
the same g-function borehole heat exchanger as GSHPB.

Overview
========

The class is :class:`tmhp.GroundSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

For quick parametric studies that do not need the full refrigerant
cycle, :class:`tmhp.GroundSourceHeatPumpEmpirical` provides a simpler
EnergyPlus EquationFit COP model with the same borehole-response
backbone.

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
:doc:`ashp`. The indoor-unit load ``Q_r_iu`` selects operating mode:
positive values are cooling, negative values are heating, and zero
values are off operation.

COP boundary
============

The physics-based GSHP uses the same final indoor-air delivery definition as
:doc:`ashp`: ``cop_sys [-] = abs(Q_a_iu_out - Q_a_iu_in) / E_tot``.
The actual airflow and common outdoor reference temperature define both
air energy flows. The final outlet includes indoor fan heat once. The
denominator retains the compressor, indoor fan and **entire ground-loop
pump** electricity; this boundary may differ from a standard unit rating.
``Q_r_iu``, rated capacity, PLR and the optimisation remain coil-duty based;
``cop_ref [-]`` remains coil duty divided by compressor electricity.
Active zero-flow or opposite-mode air delivery is flagged by
``indoor_heat_direction_valid = False`` and reported with NaN system COP.
Off states have zero air energy flows and NaN COP.

Seasonal performance uses integrated actual air delivery divided by integrated
system electricity, separately by mode. Legacy outputs through ``c5d673e``
use coil-duty COP; regenerate them before comparing to the final-air metric.
The empirical alternative and DHW models retain their existing definitions.

Empirical alternative
=====================

.. autoclass:: tmhp.GroundSourceHeatPumpEmpirical
    :members:
    :show-inheritance:
    :no-index:

API reference
=============

.. automodule:: tmhp.ground_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshp_empirical
    :members:
    :undoc-members:
    :show-inheritance:

See :doc:`ground-flow` for opt-in flow-dependent component physics, optimal pump
control, source boundaries, and feasibility diagnostics.
