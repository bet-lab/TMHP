.. Thermodynamic Models for Heat Pumps documentation master file

===============================================
TMHP: Thermodynamic Models for Heat Pumps
===============================================

A physics-based Python library for heat pump simulation.

.. rst-class:: lead

   First-principles dynamic models for heat pumps: air-, ground-,
   and water-source DHW boilers plus air- and ground-source
   space-conditioning heat pumps for heating and cooling.

TMHP's released cycle-resolved families solve the closed refrigerant
cycle at every time step using `CoolProp <http://www.coolprop.org>`_
as the equation-of-state backend. The shared core applies across
refrigerants and operating envelopes, with no manufacturer-specific
curve fits and no per-unit recalibration.

.. container:: hero-badges

   .. image:: https://img.shields.io/badge/python-3.10--3.13-3776ab?logo=python&logoColor=white
      :alt: Python 3.10 – 3.13
      :target: https://www.python.org/downloads/

   .. image:: https://img.shields.io/badge/license-MIT-success
      :alt: MIT License
      :target: https://github.com/bet-lab/TMHP/blob/main/LICENSE

   .. image:: https://img.shields.io/badge/status-alpha-orange
      :alt: Development status — Alpha

.. container:: hero-cta

   :doc:`Install <getting-started/installation>`
   :doc:`Quick start <getting-started/quickstart>`
   :doc:`Integrations <integrations/index>`
   :doc:`Validation <validation/index>`
   `GitHub <https://github.com/bet-lab/TMHP>`_

.. grid:: 1 2 2 3
    :gutter: 3
    :class-container: landing-cards

    .. grid-item-card:: Getting Started
        :link: getting-started/index
        :link-type: doc

        Install with ``uv``, run your first steady-state, then
        drive a 24-hour dynamic simulation.

    .. grid-item-card:: Concepts
        :link: concepts/index
        :link-type: doc

        Why first-principles, how the cycle is assembled, and
        how to read the diagnostic flags every call returns.

    .. grid-item-card:: Models
        :link: models/index
        :link-type: doc

        Released source/sink model families: ASHPB, GSHPB, and
        WSHPB for DHW; ASHP and GSHP for space heating/cooling.

    .. grid-item-card:: Tutorials
        :link: tutorials/index
        :link-type: doc

        Focused walkthroughs — swap refrigerants, drive realistic
        schedules, compose PV / STC / ESS subsystems.

    .. grid-item-card:: Integrations
        :link: integrations/index
        :link-type: doc

        Couple TMHP to EnergyPlus Python Plugins or export the
        current ASHPB reference adapter as FMI 2.0 / FMI 3.0
        co-simulation FMUs.

    .. grid-item-card:: API Reference
        :link: api/index
        :link-type: doc

        Every model, support module, optional integration adapter,
        and helper exposed by the TMHP package, with full type
        signatures.

    .. grid-item-card:: Validation
        :link: validation/index
        :link-type: doc

        The ASHPB reference case benchmarked against 15 Samsung
        EHS catalogue points — parity plot, per-point table,
        reproducibility.

    .. grid-item-card:: Visualize
        :link: tutorials/visualize-the-cycle
        :link-type: doc

        Plot a solved refrigerant cycle on a P–h chart using
        only CoolProp and Matplotlib.

.. toctree::
   :maxdepth: 2
   :caption: Documentation
   :hidden:

   getting-started/index
   concepts/index
   models/index
   tutorials/index
   integrations/index
   api/index
   validation/index

.. toctree::
   :maxdepth: 1
   :caption: Project Links
   :hidden:

   GitHub Repository <https://github.com/bet-lab/TMHP>
   Sister project — Energy-Exergy Analysis Engine <https://github.com/bet-lab/enex-analysis-engine>
