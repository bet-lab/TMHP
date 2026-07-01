============
Integrations
============

TMHP can stay inside a Python study, but it is also built to sit behind
building-energy simulators and co-simulation masters. The integration
adapters are optional, so the core heat-pump package stays lightweight:
``import tmhp`` for native Python simulation, then opt into the one adapter
that matches the tool on the other side of the boundary.

The whole idea is **model reuse**. TMHP keeps the cycle-resolved heat-pump
physics in one package, while each external tool keeps doing what it is
already good at: EnergyPlus owns whole-building loads and plant dispatch,
Modelica tools own equation-based HVAC and controls, and FMI masters own
tool-to-tool scheduling. The adapters on this page currently expose the
ASHPB reference boundary; the broader model family remains available
natively in Python.

Model core and adapter seams
============================

There are two layers to keep separate. The released cycle-resolved model
families can be used directly in Python through their public
``analyze_steady()`` and ``analyze_dynamic()`` methods. The optional
external adapters currently expose the ASHPB reference boundary:
EnergyPlus enters through a steady plant-component seam, and FMI enters
through the ASHPB dynamic ``step()`` seam.

.. raw:: html

   <div class="tmhp-diagram" data-diagram="hero"></div>

Read the diagram left to right as an adapter view of ASHPB. Each external
driver hands TMHP a small set of boundary variables, those variables cross
a public seam, and the shared core solves one refrigerant cycle behind that
seam before returning heat, power, COP, tank temperature, and diagnostics.
The seam is the important part:

- The **EnergyPlus** path enters through :meth:`~tmhp.AirSourceHeatPumpBoiler.analyze_steady`.
  EnergyPlus already owns the plant loop, the storage tank, the timestep, and
  the meters, so the current ASHPB reference adapter only needs a *steady*
  answer for the current conditions.
- The **FMI** path enters through :meth:`~tmhp.AirSourceHeatPumpBoiler.step`.
  Here the FMU owns the dynamic tank state and advances it one communication
  step at a time, while the master owns the schedule. This is the current
  ASHPB dynamic adapter, not a statement that the refrigerant-cycle core is
  ASHPB-only.

Keeping those seams separate is deliberate: it stops an EnergyPlus tank and an
FMU tank from ever being mistaken for one another, and it leaves a clear path
for future heat-pump families to reuse the same kind of integration boundary.

What integrations enable
========================

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Keep EnergyPlus as the building model

        Replace an empirical plant component with a TMHP steady
        refrigerant-cycle solve while EnergyPlus still owns the IDF,
        schedules, plant loop, and reporting.

    .. grid-item-card:: Export the heat pump as a reusable FMU

        Package the current ASHPB dynamic ``step()`` boundary behind FMI
        variables so a co-simulation master can set weather and DHW draw
        inputs and read power, heat, COP, tank temperature, and diagnostics.

    .. grid-item-card:: Connect to other simulation ecosystems

        Use the current ASHPB adapter in Python smoke tests, Modelica-based
        plant and controls studies, Simulink controller workflows, or
        composite FMU co-simulation while keeping the cycle physics in TMHP.

Two adapter paths are currently supported:

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: EnergyPlus Python Plugin
        :link: energyplus-python
        :link-type: doc

        Use TMHP as a ``PlantComponent:UserDefined`` surrogate. EnergyPlus
        owns the plant loop and tank state; TMHP answers each plant-solver
        call through ``analyze_steady()``.

    .. grid-item-card:: FMI FMU
        :link: fmu
        :link-type: doc

        Export the current ASHPB dynamic ``step()`` boundary as a
        co-simulation FMU with explicit input, output, unit, and diagnostic boundaries.
        TMHP provides separate FMI 2.0 and FMI 3.0 artifacts.

Integration vocabulary
======================

**EnergyPlus** is a whole-building energy simulation program. Its Python
Plugin interface lets EnergyPlus call user-defined Python classes at specific
points during a run; a plugin is just a class derived from
``EnergyPlusPlugin`` whose overridden methods decide when EnergyPlus calls
your code (`EnergyPlus Python Plugin documentation
<https://bigladdersoftware.com/epx/docs/9-3/input-output-reference/group-python-plugins.html>`_).
TMHP uses that mechanism to answer a plant-component request without moving
the building model out of EnergyPlus.

**FMI**, the Functional Mock-up Interface, is a Modelica Association standard
for exchanging dynamic models between tools. An **FMU** (Functional Mock-up
Unit) is the packaged model: a ZIP archive with XML metadata, binaries, and
source exposed through the FMI API. FMI defines several interface types; TMHP
exports *Co-Simulation* FMUs, where the importing tool sets scalar inputs,
calls ``do_step``, and reads scalar outputs (`FMI specification
<https://fmi-standard.org/docs/main/>`_).

**FMI 3.0** is a separate major version, not a container that automatically
includes FMI 2.0. The 3.0 specification adds features such as Scheduled
Execution, clocks, early return, and array variables (`FMI 3.0.2 specification
<https://fmi-standard.org/docs/3.0.2/>`_). TMHP therefore ships two adapters
over the current ASHPB ``step()`` boundary — see :doc:`fmu` for how they differ.

The reason this matters is reach. The FMI project's tools catalog covers
hundreds of FMI-capable tools (`FMI tools <https://fmi-standard.org/tools/>`_),
and a 2025 project note reported 250 listed tools, including 178
Co-Simulation importers (`FMI tools milestone
<https://fmi-standard.org/news/2025-07-14-fmi-supported-by-250-tools/>`_).
Actual compatibility still depends on the importer and on the Python runtime
that TMHP's PythonFMU-based package needs, but the boundary is the standard
FMI one rather than a TMHP-specific protocol.

Which path should I use?
========================

Use :doc:`energyplus-python` when EnergyPlus already owns the plant loop,
schedule, and tank objects, and you want TMHP to replace an empirical
heat-pump curve with a refrigerant-cycle-resolved answer.

Use :doc:`fmu` when an FMI master should own the co-simulation schedule and
TMHP should advance the current ASHPB reference state across each communication
step. This path is best for tool-to-tool coupling, and for comparing the same
dynamic boundary against native Python ``analyze_dynamic()`` runs.

Examples of FMU host workflows
==============================

.. list-table::
   :header-rows: 1
   :widths: 28 34 38

   * - Host ecosystem
     - Why it matters
     - Example use with TMHP
   * - `Modelica Buildings Library
       <https://simulationresearch.lbl.gov/modelica/>`_
     - LBNL's Buildings library provides dynamic models for building,
       district-energy, HVAC, storage, and control systems, and its
       project materials explicitly cover Spawn/EnergyPlus coupling.
     - Use a Modelica plant and controls model around a TMHP heat-pump
       FMU, or compare TMHP against a Modelica heat-pump model in a
       district-energy study.
   * - `Spawn of EnergyPlus
       <https://www.energy.gov/cmei/buildings/articles/spawn-energyplus-spawn>`_
       and `EnergyPlusToFMU
       <https://simulationresearch.lbl.gov/fmu/EnergyPlus/export/>`_
     - Spawn combines EnergyPlus loads/envelope with Modelica controls
       through FMI-based co-simulation; EnergyPlusToFMU exports
       EnergyPlus as an FMU for co-simulation.
     - Co-simulate an EnergyPlus envelope FMU with a TMHP heat-pump FMU
       and a supervisory controller, keeping each domain in the tool that
       models it best.
   * - `OpenModelica / OMSimulator
       <https://openmodelica.org/doc/OpenModelicaUsersGuide/v1.12.0/omsimulator.html>`_
       and `Dymola
       <https://www.3ds.com/products/catia/dymola/export-capabilities-interfacing-other-software>`_
     - Modelica tools can import/export FMUs and build composite
       co-simulation models that mix Modelica and non-Modelica
       submodels.
     - Put TMHP's cycle-resolved ASHPB reference FMU next to Modelica hydronic loops,
       tanks, district plants, or controllers.
   * - `FMPy <https://github.com/CATIA-Systems/FMPy>`_
     - FMPy is a Python library and GUI for inspecting and simulating
       FMUs across FMI major versions, including Co-Simulation FMUs.
     - Run local smoke tests, parameter sweeps, notebooks, and regression
       comparisons between FMU output and native TMHP Python output.
   * - `Simulink FMU block
       <https://www.mathworks.com/help/simulink/ref_extras/fmu.html>`_
     - Simulink can import FMUs and run Co-Simulation FMUs as external
       components in controller-oriented models.
     - Couple TMHP to controller prototypes or hardware-in-the-loop style
       experiments while keeping the heat-pump physics in the FMU.

.. toctree::
   :maxdepth: 1
   :hidden:

   energyplus-python
   fmu
