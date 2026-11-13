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

Defaults that follow the capacity
=================================

``GroundSourceHeatPump(hp_capacity=..., ref=...)`` is a complete machine.
Everything on the ground side and the indoor airflow is sized from the
capacity, so the rating-point COP is the same from 1 to 100 kW:

.. list-table::
   :header-rows: 1
   :widths: 26 44 30

   * - Input left out
     - Default
     - 4 kW / 30 kW
   * - ``ground_flow_ref_lpm``
     - ``0.75 × hp_capacity / (ρc × 3 K)``: the rated source-side heat
       changes the loop temperature by 3 K (about 4.8 K in cooling, where
       the loop carries 1.2 × the capacity). Parallel boreholes share it
       equally.
     - 14.3 / 107.5 L/min
   * - ``E_pmp``
     - ASHRAE pumping grade B, 75 W per ton (21.3 W per kW) of capacity.
       Used when the hydraulic pump model is off.
     - 85 / 640 W
   * - ``N_1``, ``N_2``, ``H_b`` (all three)
     - Smallest near-square field of 100 m boreholes that keeps the rated
       source-side heat within 50 W/m.
     - 1 × 1 / 3 × 2
   * - ``dV_iu_fan_a_rated``
     - 5.37e-5 m³/s per W (400 cfm per ton).
     - 773 / 5 800 m³/h

The helpers are :func:`tmhp.ground_defaults.default_ground_flow_lpm`,
:func:`tmhp.ground_defaults.default_borefield_layout` and
:func:`tmhp.pump.default_ground_pump_power`. A value you supply replaces its
default. Giving any one of ``N_1``, ``N_2`` or ``H_b`` fixes the borefield as
given (the others take 1, 1 and 100 m); the flow and the pump power still
follow the capacity unless you give them too.

Two sizing slips are reported with a ``UserWarning`` at construction instead
of being solved silently:

* a U-tube that is **laminar** at the reference flow (``Re < 2300``, for
  example a flow that was not scaled with the number of parallel boreholes).
  The borehole resistance is evaluated for laminar flow (film coefficient
  ``Nu = 3.66``, interpolated to the turbulent correlation between
  ``Re`` 2300 and 4000) and roughly doubles. The Reynolds number is the
  model attribute ``Re_bhe_ref``.
* a borefield you sized that carries more than **80 W/m** at the rated
  source-side heat (attribute ``q_bhe_rated``).

At the rated duty the default indoor coil (``UA_iu_rated = 0.08 ×
hp_capacity``) at this airflow needs an indoor approach of 21.6 K: supply air
at 35 °C and condensing at 41.6 °C in heating from a 20 °C room, supply air
at 12 °C and evaporating at 5.4 °C in cooling from 27 °C (the coil is dry).
``indoor_approach_max_K`` therefore defaults to 25 K.

.. note::

   Until November 2026 the defaults were fixed at the values of the 4 kW
   machine (20.04 L/min, 100 W, one borehole) and the indoor airflow was
   0.0002 m³/s per W (2 880 m³/h for 4 kW). A larger machine then ran on the
   small loop: at 30 kW the system COP of rated heating against a 0 °C
   borehole wall fell from 3.8 to 2.1 and some rating points had no
   solution; 100 kW could not be constructed. To reproduce
   results from before, pass ``ground_flow_ref_lpm=20.04, E_pmp=100, N_1=1,
   N_2=1, dV_iu_fan_a_rated=0.0002 * hp_capacity,
   indoor_approach_max_K=20``. Fan limits given as absolute flows
   (``dV_iu_fan_a_min/max``) are read against the new reference airflow
   unless ``dV_iu_fan_a_rated`` is given as well.

Source-side mechanics
=====================

Same g-function-based borehole as :doc:`gshpb`. See that page for the
detailed mechanic and the g-function figure.

In ``analyze_dynamic`` the ground fluid is closed inside each step: the
borehole wall of step ``n`` is the step mean of its response to every load
so far, the step's own included, and the fluid temperature follows from it
through ``R_b*`` and the loop flow. The cycle of a row is solved with the
``T_bhe_f_out [°C]`` reported in that row, so a heating row always has its
evaporating temperature below the fluid and a cooling row its condensing
temperature above it. (Earlier versions solved the cycle with the fluid left
by the previous step; after an off period the first step then ran on
undisturbed ground.)

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

:class:`tmhp.GroundSourceHeatPumpEmpirical` uses the ClimateMaster TCH072
ground-loop curves shipped with EnergyPlus (``WaterToAirHeatPumps.idf``). The
curves give full-load capacity and power at the rated air flow, so the COP
does not change with part load (no cycling degradation is applied) and a
request above the available capacity ``Q_cap`` is served at that ceiling
with ``capacity_clamped = "max"``. Outside the fitted range — entering water
−1.25 to 32.06 °C in heating and 9.85 to 48.72 °C in cooling, entering air
9.85 to 26.51 °C dry-bulb in heating and 9.85 to 23.73 °C wet-bulb in
cooling — the unit does not run for that step and ``failure_reason`` is
``"outside_curve_domain"``. The water temperature is judged at the
converged entering water of the step, so cold ground that the step's own
heat rejection brings into range (or warm ground in heating) still runs. The room (entering-air) temperature is the
``T_a_room`` input (27 / 21 °C when omitted).

Its conventions differ from the refrigerant-cycle model: the borehole load
``q_b`` is injection-positive W/m, temperatures ``T_b``, ``T_f``, ``T_f_in``
and ``T_f_out`` are in kelvin, ``COP`` is 0 when idle, the entering-water
temperature is the mean fluid temperature minus half the loop temperature
change, and refrigerant temperatures for the exergy terms use fixed
approaches (±3 K to the ground fluid, ∓15 K to the room air).

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
