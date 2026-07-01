======
Models
======

System-level heat pump models — the classes you instantiate directly.
Each page below is a 1-stop reference for one model family: how it
plugs the shared refrigerant cycle into a specific source / sink
pairing, what the system-specific mechanics look like, how to compose
subsystems (STC, PV + ESS) on top, and the full API reference.

The families are peers around one cycle core, but the docs distinguish
implemented public APIs from future source/sink combinations. The
released combinations are ASHPB, GSHPB, and WSHPB for DHW tanks, plus
ASHP and GSHP for building heating/cooling loads. Getting Started uses
the ASHPB reference case because it is validated and has the smallest
input surface, not because the library is limited to heat-pump boilers.

.. figure:: ../_static/source_sink_matrix.svg
    :alt: TMHP released source and sink matrix showing ASHPB, GSHPB,
        and WSHPB for DHW and ASHP and GSHP for space conditioning.
    :align: center
    :width: 100%

    TMHP model families differ by released source/sink boundary while
    sharing the same refrigerant-cycle calculations and diagnostics.

Code-backed family matrix
=========================

.. list-table::
    :header-rows: 1
    :widths: 24 18 22 18 18

    * - Public class
      - Source boundary
      - Demand boundary
      - Operating mode
      - Public time boundary
    * - :class:`tmhp.AirSourceHeatPumpBoiler`
      - Outdoor air coil
      - DHW tank charge
      - Heating / tank charge
      - ``analyze_steady()``, ``analyze_dynamic()``, ``step()``
    * - :class:`tmhp.GroundSourceHeatPumpBoiler`
      - Borehole field
      - DHW tank charge
      - Heating / tank charge
      - ``analyze_steady()``, ``analyze_dynamic()``; point-state
        ``step()`` is intentionally unavailable because the borehole
        response is history-dependent.
    * - :class:`tmhp.WaterSourceHeatPumpBoiler`
      - Prescribed water loop
      - DHW tank charge
      - Heating / tank charge
      - ``analyze_steady()``, ``analyze_dynamic()``
    * - :class:`tmhp.AirSourceHeatPump`
      - Outdoor air coil
      - Indoor-unit building load
      - ``Q_r_iu > 0`` cooling, ``Q_r_iu < 0`` heating
      - ``analyze_steady()``, ``analyze_dynamic()``
    * - :class:`tmhp.GroundSourceHeatPump`
      - Borehole field
      - Indoor-unit building load
      - ``Q_r_iu > 0`` cooling, ``Q_r_iu < 0`` heating
      - ``analyze_steady()``, ``analyze_dynamic()``

Only :class:`tmhp.AirSourceHeatPumpBoiler` has a quantitative catalogue
validation page today. The other cycle-resolved families share the same
refrigerant-cycle core and have smoke coverage on representative
operating points. :class:`tmhp.GroundSourceHeatPumpEmpirical` is
documented on the GSHP page as a faster EquationFit alternative; it is
not one of the refrigerant-cycle-core families in the matrix above.

.. grid:: 2
    :gutter: 3

    .. grid-item-card:: Air-source heat pump boiler
        :link: ashpb
        :link-type: doc

        ASHPB core + STC preheat, STC stratified tank, PV + ESS
        composed variants. The validated reference example.

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
