=============
API Reference
=============

Reference for the TMHP package's support modules — the
lower-level building blocks the system models compose internally.
For the system-level models you instantiate directly (ASHPB, GSHPB,
WSHPB, ASHP, GSHP), see :doc:`../models/index`.

Support modules
===============

Lower-level building blocks used by the system models.

.. grid:: 2
    :gutter: 3

    .. grid-item-card:: Refrigerant & thermodynamics
        :link: support/refrigerant-thermo
        :link-type: doc

        CoolProp state-point helpers, cycle analysis, and
        COP correlations.

    .. grid-item-card:: Heat transfer & exchangers
        :link: support/heat-transfer
        :link-type: doc

        ε-NTU heat exchanger model, air-side fan and
        heat-exchanger calculations, and borehole g-function.

    .. grid-item-card:: Demand & weather
        :link: support/demand-weather
        :link-type: doc

        Outdoor air temperature utilities and domestic hot
        water demand profiles.

    .. grid-item-card:: Subsystems
        :link: support/subsystems
        :link-type: doc

        Solar thermal collector, photovoltaic system,
        energy storage system, and UV treatment.

    .. grid-item-card:: Simulation helpers
        :link: support/simulation
        :link-type: doc

        Per-step dynamic context, energy / exergy helpers,
        and stdout summary tables.

    .. grid-item-card:: Integrations
        :link: support/integrations
        :link-type: doc

        Implementation reference for the FMI co-simulation and
        EnergyPlus Python Plugin adapters. For usage guides, see
        :doc:`../integrations/index`.

    .. grid-item-card:: Visualization
        :link: support/visualization
        :link-type: doc

        Plotting facade and Mollier (T-h / P-h / T-s)
        diagrams.

    .. grid-item-card:: Utilities & constants
        :link: support/utilities
        :link-type: doc

        Unit-conversion helpers and physical constants.

.. toctree::
    :maxdepth: 2
    :hidden:

    support/index
