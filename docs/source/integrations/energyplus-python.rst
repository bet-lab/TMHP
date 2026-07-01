========================
EnergyPlus Python Plugin
========================

TMHP's current EnergyPlus adapter exposes
:class:`~tmhp.AirSourceHeatPumpBoiler` as a
``PlantComponent:UserDefined`` surrogate. EnergyPlus still owns the
plant loop, timestep, load dispatch, and storage-tank objects; TMHP
answers each plant-solver call with a refrigerant-cycle-resolved steady
state through :meth:`~tmhp.AirSourceHeatPumpBoiler.analyze_steady`.

Use this path when you have an EnergyPlus model and want the heat pump
to be more physical than an empirical catalogue curve fit, without
moving the whole building simulation out of EnergyPlus. This page is
specific to the ASHPB reference adapter that exists today; it should not
be read as a limit on TMHP's shared refrigerant-cycle core.

What EnergyPlus Python Plugins are
==================================

EnergyPlus Python Plugins are user-defined Python classes that inherit
from EnergyPlus's ``EnergyPlusPlugin`` base class and override named
methods for specific simulation calling points. In plugin mode,
EnergyPlus is still launched as a normal EnergyPlus simulation; it
starts an embedded Python interpreter and calls the plugin when the IDF
declares the relevant plugin instance and calling manager (`EnergyPlus
Python Plugin documentation
<https://bigladdersoftware.com/epx/docs/9-3/input-output-reference/group-python-plugins.html>`_).

The broader EnergyPlus Python API exposes functional, runtime, and data
exchange interfaces. The data exchange API is the part TMHP uses here:
it reads plant-loop values and writes actuators or plugin globals during
runtime callback methods (`EnergyPlus Python API
<https://energyplus.readthedocs.io/en/latest/api.html>`_).

For TMHP, this is a narrow and useful boundary. EnergyPlus keeps the
IDF, weather file, schedules, plant loop iteration, storage tank, sizing,
and reporting. The current ASHPB adapter only replaces the heat-pump
component response with a cycle-resolved steady solve, then returns
outlet temperature, mass-flow request, electricity, and diagnostics to
EnergyPlus.

Runtime contract
================

EnergyPlus Python Plugins run inside EnergyPlus's embedded CPython.
That means ``pyenergyplus`` comes from the EnergyPlus installation, not
from PyPI, and TMHP plus its native dependencies must be importable by
that embedded interpreter.

.. code-block:: text

   EnergyPlus plant solver
       -> PythonPlugin:SearchPaths
       -> tmhp.integrations.energyplus_plugin
       -> AirSourceHeatPumpBoiler.analyze_steady()

Practical setup:

1. Install TMHP into an environment compatible with the EnergyPlus
   embedded Python ABI.
2. Add that environment or package path with ``PythonPlugin:SearchPaths``.
3. Verify an import-only smoke plugin first. A wrong-ABI native wheel
   such as CoolProp can fail before the plant callback reaches useful
   logging.

What this enables
=================

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Workflow
     - Why the adapter matters
   * - Replace a catalogue curve
     - Keep the same EnergyPlus plant model, but substitute a
       refrigerant-cycle TMHP solve for an empirical heat-pump
       performance curve.
   * - Compare refrigerants in context
     - Run the same building and plant dispatch against different
       CoolProp-supported refrigerants without re-fitting component
       curves for every candidate.
   * - Preserve EnergyPlus reporting
     - Leave EnergyPlus responsible for meters, schedules, plant loop
       iteration, and custom output variables while TMHP exposes
       compressor energy, power, convergence, and failure diagnostics.

The payoff is concrete: the whole EnergyPlus building stays exactly as it is,
and the empirical catalogue curve in the plant slot is replaced by the current
ASHPB refrigerant-cycle solve — so you can compare refrigerants in the same
building and dispatch without re-fitting a performance curve for each
candidate.

.. raw:: html

   <div class="tmhp-diagram" data-diagram="ep-example"></div>

How one plant call works
========================

EnergyPlus owns the plant loop, the storage tank, and the timestep. On each
plant-solver call it hands the plugin the loop boundary values, and the plugin
answers with a single steady cycle solve. (A one-shot sizing call runs once
before the simulation to size the plant connection.) Walk through one call
step by step:

.. raw:: html

   <div class="tmhp-diagram" data-diagram="ep-seq"></div>

The plugin memoizes the solve on rounded inputs, because the plant solver
re-calls it with identical inputs many times per timestep, and it converts the
delivered heat into the outlet-temperature actuator with
``T_out = T_in + Q_ref_tank / (mdot * cp)``.

IDF wiring
==========

The adapter expects one ``PlantComponent:UserDefined`` component and two
Python plugin managers:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - EnergyPlus object
     - TMHP binding
   * - ``PlantComponent:UserDefined``
     - Default name ``ASHPB_UserDefined`` unless ``TMHP_UD_NAME`` is set.
   * - Initialization program-calling manager
     - :class:`tmhp.integrations.energyplus_plugin.TmhpPlantInit`
       sizes the plant connection so EnergyPlus can dispatch load.
   * - Simulation program-calling manager
     - :class:`tmhp.integrations.energyplus_plugin.TmhpPlantSurrogate`
       reads loop boundary values, solves TMHP, and writes actuators.
   * - ``PythonPlugin:Variables``
     - Declare ``tmhp_E_cmp_J`` for timestep energy. Optionally declare
       ``tmhp_E_cmp_W`` for instantaneous compressor power.

Input / output boundary
=======================

The plugin reads only finite EnergyPlus boundary values before calling
TMHP. Invalid inputs are reported through ``issue_severe()``, the plant
component is driven to a safe off state, and the callback returns a
non-zero status instead of computing with bad values.

.. list-table::
   :header-rows: 1
   :widths: 26 30 44

   * - Direction
     - Field
     - Meaning
   * - EnergyPlus -> TMHP
     - Inlet temperature
     - Plant Connection 1 inlet water temperature.
   * - EnergyPlus -> TMHP
     - Inlet mass flow rate
     - Current loop-side flow available to the user-defined component.
   * - EnergyPlus -> TMHP
     - Inlet specific heat
     - Loop fluid heat capacity used to convert heat rate to outlet
       temperature.
   * - EnergyPlus -> TMHP
     - Load request
     - Positive heating load request for the plant component.
   * - EnergyPlus -> TMHP
     - Outdoor drybulb temperature
     - Source-side air temperature ``T0`` for the ASHPB cycle solve.
   * - TMHP -> EnergyPlus
     - Outlet temperature actuator
     - Inlet temperature plus delivered heat divided by ``m_dot * cp``,
       clamped inside the liquid-water property range.
   * - TMHP -> EnergyPlus
     - Mass-flow actuator
     - Requests design flow when load is present and EnergyPlus has not
       yet provided loop flow.
   * - TMHP -> EnergyPlus
     - ``tmhp_E_cmp_J``
     - Timestep compressor electricity in joules.
   * - TMHP -> EnergyPlus
     - ``tmhp_E_cmp_W``
     - Optional instantaneous compressor power in watts.

Configuration
=============

The adapter reads its configuration once at import from environment
variables:

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Variable
     - Meaning
   * - ``TMHP_ASHPB_REF``
     - Refrigerant name. Default ``R32``.
   * - ``TMHP_ASHPB_CAPACITY``
     - Nominal heat-pump capacity in watts. Default ``15000``.
   * - ``TMHP_UD_NAME``
     - ``PlantComponent:UserDefined`` object name. Default
       ``ASHPB_UserDefined``.
   * - ``TMHP_LOOP_DESIGN_VDOT``
     - Design loop volume flow in ``m3/s``. Default ``0.003``.
   * - ``TMHP_EPLUS_ECMP_ENERGY_GLOBAL``
     - Plugin global receiving timestep energy in joules. Default
       ``tmhp_E_cmp_J``.
   * - ``TMHP_EPLUS_ECMP_POWER_GLOBAL``
     - Optional plugin global receiving instantaneous power in watts.
       Default ``tmhp_E_cmp_W``.
   * - ``TMHP_PLUGIN_LOG``
     - Optional path for per-call and convergence-tally logging.

Why this uses ``analyze_steady()``
==================================

EnergyPlus is already the dynamic simulation environment. It owns the
plant loop iteration, the storage tank, and the timestep integration.
The plugin therefore asks TMHP for a steady component response:

.. code-block:: python

   result = hp.analyze_steady(
       T_tank_w=t_in,
       T0=t0,
       Q_ref_tank=q_target,
   )

The FMU adapter uses :meth:`~tmhp.AirSourceHeatPumpBoiler.step` instead
because the FMU owns its own dynamic state. Keeping those seams separate
prevents EnergyPlus tank state and FMU tank state from being mixed.

API reference
=============

The implementation API is documented at
:mod:`tmhp.integrations.energyplus_plugin`.
