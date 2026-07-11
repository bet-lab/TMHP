==============
FMI FMU export
==============

TMHP's current FMU adapter wraps
:class:`~tmhp.AirSourceHeatPumpBoiler` as an FMI Co-Simulation
component. The FMI master owns the communication schedule; the FMU owns
the ASHPB reference dynamic state and advances it through
:meth:`~tmhp.AirSourceHeatPumpBoiler.step` at each ``do_step`` call.

Use this path when TMHP needs to participate in a tool-level
co-simulation workflow, or when a non-Python master should drive a
cycle-resolved ASHPB reference component with explicit FMI variables.
This page documents the adapter that exists today; the broader TMHP
model family still shares the same refrigerant-cycle core across the
released DHW-boiler families and the air/ground space-conditioning
families.

FMI and FMU in one minute
=========================

FMI, the Functional Mock-up Interface, is a Modelica Association standard
for exchanging dynamic models between simulation tools. An FMU,
Functional Mock-up Unit, is the packaged model artifact: a ZIP archive
with XML metadata and implementation files exposed through the FMI API
(`FMI specification <https://fmi-standard.org/docs/main/>`_).

FMI separates several interface types. TMHP targets Co-Simulation, where
the importing tool owns the communication schedule, sets inputs at
communication points, calls ``do_step``, and reads outputs. In
Co-Simulation, the FMU abstracts its internal computation from the
importer; the importer coordinates time advancement and data exchange
across connected components (`FMI for Co-Simulation
<https://fmi-standard.org/docs/main/#fmi-for-co-simulation>`_).

FMI 3.0 is a separate major standard, not a container that automatically
includes FMI 2.0. The FMI 3.0.2 specification states compatibility in
terms of the same major version and any minor version, and adds FMI 3.0
features such as Scheduled Execution, clocks, early return, event mode,
intermediate update, array variables, and additional scalar types
(`FMI 3.0.2 specification
<https://fmi-standard.org/docs/3.0.2/>`_). For tool reach, TMHP therefore
ships two adapters over the same ASHPB reference ``step()`` seam:

- :mod:`tmhp.integrations.fmu` builds an FMI 2.0 Co-Simulation FMU with
  ``pythonfmu``. This is the conservative compatibility path.
- :mod:`tmhp.integrations.fmu3` builds an FMI 3.0 Co-Simulation FMU with
  ``pythonfmu3``. This is the modern-major-version path. It does not
  expose clocks, Scheduled Execution, or arrays because the current TMHP
  boundary is a scalar one-step heat-pump component.

Both adapters wrap the identical ``step()`` kernel and expose the same scalar
boundary — four parameters, three inputs, and eight outputs. Only the
FMI-version mechanics differ, as the diagram makes concrete:

.. raw:: html

   <div class="tmhp-diagram" data-diagram="fmi-compare"></div>

The practical benefit is tool reach. The FMI project maintains a tools
catalog across importers, exporters, platforms, and FMI versions (`FMI
tools <https://fmi-standard.org/tools/>`_). A 2025 FMI project note
reported 250 listed tools, including 178 Co-Simulation importers and 133
Co-Simulation exporters (`FMI tools milestone
<https://fmi-standard.org/news/2025-07-14-fmi-supported-by-250-tools/>`_).

What becomes possible
=====================

The FMU adapter turns the current ASHPB reference boundary from a
Python-only model into a reusable co-simulation component. That enables:

- putting the same cycle-resolved heat-pump boundary behind a Modelica plant loop, an
  EnergyPlus envelope FMU, a Python regression harness, or a controller
  model;
- comparing native Python ``analyze_dynamic()`` results against the FMU
  boundary under the same weather and draw schedules;
- running parameter sweeps or controller experiments without rewriting
  the TMHP cycle model for every host tool;
- keeping thermal physics, plant control, building loads, and
  post-processing in the tools that already model each part best.

A concrete example: wire an EnergyPlus envelope FMU, the TMHP heat-pump FMU,
and a supervisory controller together under one FMI master, and each domain
stays in the tool that models it best.

.. raw:: html

   <div class="tmhp-diagram" data-diagram="fmu-example"></div>

Install the optional FMU tooling
================================

The core TMHP package does not install PythonFMU or FMPy. Add the
``integrations`` extra when building or smoke-testing the FMU:

.. code-block:: bash

   uv sync --extra integrations --locked

This installs:

- ``pythonfmu`` for building the FMI 2.0 co-simulation FMU.
- ``pythonfmu3`` for building the FMI 3.0 co-simulation FMU.
- ``fmpy`` for model-description validation and local smoke simulation.

Build and simulate
==================

Build the FMI 2.0 FMU from the repository root:

.. code-block:: bash

   uv run pythonfmu build -f src/tmhp/integrations/fmu.py .

PythonFMU writes ``TmhpAshpbSlave.fmu`` into the current output
directory.

Build the FMI 3.0 FMU separately:

.. code-block:: bash

   uv run pythonfmu3 build -f src/tmhp/integrations/fmu3.py .

PythonFMU3 writes ``TmhpAshpbFmi3Slave.fmu``. A local smoke test can then
validate and simulate either FMU through FMPy, as long as the importer
supports the FMU's FMI major version:

.. code-block:: python

   from fmpy import simulate_fmu
   from fmpy.validation import validate_fmu

   assert validate_fmu("TmhpAshpbSlave.fmu") == []

   result = simulate_fmu(
       "TmhpAshpbSlave.fmu",
       stop_time=3600.0,
       output=[
           "E_cmp",
           "E_tot",
           "Q_ref_tank",
           "cop_sys",
           "T_tank_w",
           "converged",
           "failure_reason",
       ],
   )

How one communication step works
================================

The master owns the schedule; the FMU owns the ASHPB reference state. On
each communication step the master sets the input variables, calls
``do_step``, and reads the outputs, while the adapter advances the
cycle-resolved core by exactly one ``step()`` call. Walk through it one
message at a time:

.. raw:: html

   <div class="tmhp-diagram" data-diagram="fmu-seq"></div>

The headline difference between the two adapters lives in that ``do_step``
return: the FMI 2.0 slave returns a bare ``bool``, while the FMI 3.0 slave
returns an ``Fmi3StepResult`` and can signal an invalid input as a discarded
step with early return. Either way, ``step()`` is the only TMHP call per
communication step, which is what keeps the FMU output aligned with a native
``analyze_dynamic()`` run.

Runtime contract
================

The FMU is a tool-coupling artifact, not a hermetic binary. The
importing environment must provide a compatible Python runtime plus
TMHP, CoolProp, NumPy, SciPy, and the other native dependencies for the
target operating system, architecture, and Python ABI.

Both adapters intentionally target Co-Simulation only:

- TMHP exposes no continuous state derivatives for FMI model exchange.
- The ASHPB reference state is advanced in one pass for each communication step.
- Save-state and rollback support are outside the current adapter scope.
- FMI outputs are sanitized so NaN or infinity does not cross the
  importer boundary.

The FMI 3.0 adapter returns ``Fmi3StepResult`` and can signal invalid
input as a discarded step with early return. It otherwise exposes the
same scalar boundary as the FMI 2.0 adapter so regression tests can
compare both FMUs against the same native ``analyze_dynamic()`` schedule.

Compatible host examples
========================

An FMU is useful only if the importing environment can load both the FMI
interface and the runtime dependencies of the packaged model. TMHP's
current PythonFMU-based FMU is therefore best treated as a transparent
co-simulation package that still needs a compatible Python environment.
Within that constraint, the same FMU boundary can be used in several
well-established ecosystems:

.. list-table::
   :header-rows: 1
   :widths: 26 34 40

   * - Host ecosystem
     - Official capability
     - Example TMHP use
   * - `Modelica Buildings Library
       <https://simulationresearch.lbl.gov/modelica/>`_
     - LBNL's Buildings library provides open-source dynamic models for
       buildings, district energy systems, storage, HVAC, and controls,
       and documents use cases such as rapid prototyping, integrated
       energy-system testing, controls development, and Spawn/EnergyPlus
       coupling.
     - Place a TMHP ASHPB reference FMU inside a Modelica hydronic plant, compare
       it with Modelica-native heat-pump models, or study refrigerant
       choices in a district-energy controls scenario.
   * - `Spawn of EnergyPlus
       <https://www.energy.gov/cmei/buildings/articles/spawn-energyplus-spawn>`_
       and `EnergyPlusToFMU
       <https://simulationresearch.lbl.gov/fmu/EnergyPlus/export/>`_
     - Spawn is described by DOE as a BEM-controls engine based on FMI
       and Modelica; EnergyPlusToFMU exports EnergyPlus 8.0+ models as
       co-simulation FMUs that can be linked to system models such as
       Modelica/Dymola HVAC models.
     - Use EnergyPlus for loads and envelope, TMHP for heat-pump
       thermodynamics, and a separate controller or plant model for
       supervisory logic.
   * - `OpenModelica / OMSimulator
       <https://openmodelica.org/doc/OpenModelicaUsersGuide/v1.12.0/omsimulator.html>`_
       and `Dymola
       <https://www.3ds.com/products/catia/dymola/export-capabilities-interfacing-other-software>`_
     - Modelica toolchains can import FMUs and build composite
       co-simulation models that combine Modelica and non-Modelica
       submodels.
     - Couple TMHP with equation-based tanks, hydronic loops, storage,
       district plants, or control sequences without translating TMHP to
       Modelica.
   * - `FMPy <https://github.com/CATIA-Systems/FMPy>`_
     - FMPy is a Python library, GUI, CLI, and notebook-oriented tool for
       inspecting and simulating FMUs across FMI 1.0, 2.0, and 3.0.
     - Validate the exported FMU, run local smoke simulations, and keep
       regression tests aligned with native TMHP Python simulations.
   * - `Simulink FMU block
       <https://www.mathworks.com/help/simulink/ref_extras/fmu.html>`_
     - Simulink can import FMUs; its Co-Simulation mode integrates FMUs
       that may contain local solvers for tool coupling.
     - Drive TMHP from controller prototypes, supervisory logic, or
       hardware-in-the-loop style experiments while preserving the same
       heat-pump FMU interface.

Input / output boundary
=======================

The FMU declares units in ``modelDescription.xml`` for power,
temperature, volume flow, and dimensionless COP. Outputs are also listed
in ``ModelStructure/InitialUnknowns`` so importers can resolve the
initial dependency set.

.. list-table::
   :header-rows: 1
   :widths: 22 26 52

   * - Causality
     - Variable
     - Meaning
   * - Parameter
     - ``ref``
     - Refrigerant name. Default ``R32``.
   * - Parameter
     - ``hp_capacity``
     - Nominal heat-pump capacity in watts.
   * - Parameter
     - ``T_tank_w_init``
     - Initial tank-water temperature in degrees Celsius.
   * - Parameter
     - ``T_sur``
     - Surrounding temperature for tank losses in degrees Celsius.
   * - Input
     - ``T0``
     - Outdoor air temperature in degrees Celsius.
   * - Input
     - ``dhw_draw``
     - Service-water draw-off in ``m3/s``.
   * - Input
     - ``T_sup_w``
     - Mains make-up water temperature in degrees Celsius.
   * - Output
     - ``E_cmp``
     - Compressor electric power in watts.
   * - Output
     - ``E_tot``
     - Total system electric power in watts.
   * - Output
     - ``Q_ref_tank``
     - Refrigerant-to-tank heat transfer in watts.
   * - Output
     - ``cop_sys``
     - System COP including auxiliary loads.
   * - Output
     - ``T_tank_w``
     - Updated tank-water temperature in degrees Celsius.
   * - Output
     - ``hp_is_on``
     - Whether the heat pump is active for this step.
   * - Output
     - ``converged``
     - Whether the TMHP cycle solve accepted the step result.
   * - Output
     - ``failure_reason``
     - Step-level diagnostic reason, or ``none``.

Invalid importer inputs
=======================

Before advancing the internal state, the slave rejects non-finite time
or input values, non-positive communication step sizes, and negative
``dhw_draw``. In that case ``do_step`` returns ``False`` and the
diagnostic outputs are set to:

.. code-block:: text

   hp_is_on = false
   converged = false
   failure_reason = "invalid_input"

The state is not advanced for that rejected step.

Relationship to native Python simulation
========================================

The FMU path is intentionally aligned with native dynamic simulation:
each ``do_step`` call maps to one public
:meth:`~tmhp.AirSourceHeatPumpBoiler.step` call. For smoke testing,
compare FMU outputs against a native ``analyze_dynamic()`` run over the
same schedule, especially ``E_cmp``, ``E_tot``, ``Q_ref_tank``,
``cop_sys``, and ``T_tank_w``.

API reference
=============

The implementation API is documented at :mod:`tmhp.integrations.fmu`.
