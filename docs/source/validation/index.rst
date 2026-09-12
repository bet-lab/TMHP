==========
Validation
==========

A physics-based model is only useful if its first-principles answers land close
to the real machines it is meant to describe. This page is that check.

The harder question is whether it keeps landing close when the machine changes.
A model tuned to one unit will match that unit; what tells you something is
whether the same defaults, unchanged, follow a different refrigerant, a
different size, and a different equipment architecture.

.. admonition:: How to read these numbers
    :class: important

    **Nothing here is fitted to anything here.** Every unit is run with the
    library defaults exactly as shipped. The only per-unit inputs are values the
    manufacturer publishes about the machine itself — nameplate capacity,
    refrigerant, and, where the manufacturer publishes them at all, compressor
    displacement and rated air flow. Those have the same status as the
    nameplate: specification, not tuning. Where the evidence for a default came
    from is a separate page, :doc:`defaults`.

    **Points the model could not evaluate are reported, not dropped.** The
    counts below distinguish points attempted from points evaluated, and the
    interactive table shows each unevaluated point with its reason.

.. toctree::
    :hidden:

    defaults
    part-load
    adding-a-catalogue


Parity
======

.. figure:: ../_static/validation_parity.svg
    :alt: Predicted against published COP for every catalogue point, split into
        air-to-water and air-to-air panels, coloured by refrigerant and sized
        by nominal capacity, with ±10 % and ±20 % bands.
    :align: center
    :width: 100%

    Predicted against published COP. Colour is refrigerant, marker size is
    nominal capacity. A model quietly tuned to one unit would show one tight
    cluster on the diagonal and the rest scattered.

.. list-table:: Coverage
    :header-rows: 1
    :widths: 30 70

    * - Dimension
      - Span
    * - Families
      - Air-to-water (``AirSourceHeatPumpBoiler``), air-to-air
        (``AirSourceHeatPump``)
    * - Refrigerants
      - R32, R410A, R290
    * - Nominal capacity
      - 2.0 – 16.0 kW, a factor of eight
    * - Manufacturers
      - Panasonic, Samsung, Daikin, Fujitsu
    * - Points
      - 757 attempted, 747 evaluated
    * - Modes
      - Heating and cooling
    * - Source temperature
      - −20 to +40 °C
    * - Sink temperature
      - 15 to 65 °C leaving water / indoor dry bulb


Per-unit results
================

.. raw:: html

   <div id="validation-table-mount"></div>
   <script src="../_static/js/plots/_plot-common.js"></script>
   <script src="../_static/js/widgets/validation-table.js"></script>

Sort by any column, filter by unit, refrigerant or mode. The numbers come from
``validation/results/``, which the parity harness writes.


Reading the residuals
=====================

The headline number across the whole set is a COP MAPE of 16.5 % over 747
evaluated points. That number is not evenly distributed, and the way it is
distributed is the most useful thing on this page.

.. list-table::
    :header-rows: 1
    :widths: 34 14 14 38

    * - Group
      - COP MAPE
      - Bias
      - What it is
    * - Air-to-water, 10 units
      - 7.4 %
      - −2 %
      - Scatter, roughly centred
    * - Air-to-air, Daikin, 5 units
      - 9.1 %
      - +0.2 %
      - Scatter, roughly centred
    * - Air-to-air, Fujitsu, 2 units
      - 33 %
      - **+33 %**
      - Not scatter — a uniform offset

A bias equal to the error is the signature of a systematic cause, so the
Fujitsu result was checked before being published rather than after.

.. admonition:: The offset is real hardware, and it is the main limitation
    :class: important

    The two air-to-air product lines are genuinely that far apart. Nameplate
    cooling efficiency, from each manufacturer's own specification table:

    - Daikin FTXM25A (2.5 kW): **EER 5.21 W/W**
    - Fujitsu ASUH09LPAS (2.64 kW): **EER 3.66 W/W**

    A 42 % difference between machines of the same size and the same era. The
    Fujitsu capacity tables agree with its own nameplate, so this is the
    equipment, not a transcription error.

    TMHP's default conductance predicts EER 4.78 for the Daikin (8 % low) and
    4.50 for the Fujitsu (23 % high). Backing the conductance out of each
    machine's rating point, the Daikin implies roughly ``Q/4.5`` and the
    Fujitsu roughly ``Q/7``.

    **Both are inside the measured band.** The component-catalogue population
    that :doc:`the default was derived from <defaults>` spans ``UA/Q`` from
    0.114 to 0.240 at p10–p90, which is about ``Q/4`` to ``Q/9``. The band is
    wide because real hardware is wide. A single vendor-neutral default lands
    somewhere in it and cannot be right for every machine in it.

    So: **the defaults describe a typical machine at the efficient end of
    current practice.** On a high-efficiency unit they land within about
    10 %; on a budget product line they will over-predict COP by something
    like 30 %. If you are modelling a specific machine and its efficiency is
    known, pass ``UA_ou_rated`` — that is what the argument is for.

.. admonition:: Why the default was not moved
    :class: note

    Nudging the conductance from ``Q/5`` toward ``Q/6`` would have improved the
    aggregate number on this page. It was not done. The value was derived from
    the catalogue population before any parity result existed, and changing it
    afterwards to improve a parity plot is calibration wearing validation's
    clothes — the exact thing this harness is built to prevent. The spread is
    reported instead.

Three smaller patterns, also named rather than left for a reader to find:

**The air-to-water set is the tighter one.** Those units publish compressor
displacement, which no air-to-air unit in the set does — neither Daikin nor
Fujitsu prints one, and a search of five data books, three service manuals and
two specification sheets found no displacement for any of them. So the
air-to-air panel is also a test of the *derived* displacement rule rather than
of a specification input.

**Both air-to-air refrigerants are present for a reason.** Daikin's
pair-application range is entirely R-32. Without the Fujitsu R-410A units that
panel would say nothing about whether the physics carries across working
fluids. It is worth being explicit that the offset above is *not* a refrigerant
effect: the three R-410A air-to-water units are among the best fits in the
whole set, at 4.7 to 7.9 % MAPE.

**Two units in the air-to-water set stand out.** The 16 kW R32 unit runs
optimistic with a consistent positive bias, which is unexplained. The Samsung
high-temperature unit publishes no displacement and reaches 65 °C leaving water
at −10 °C outdoor air — a pressure ratio around 16, which real machines achieve
with vapour injection and TMHP does not model as a single-stage cycle. Both are
stated rather than corrected for.

.. note::

    An earlier published figure for the Samsung unit — MAE 0.35, MAPE 10.1 % —
    came from a parameter set written for that machine, including its own
    displacement, conductances and efficiency coefficients. It is not
    comparable to the number here, which is the shipped defaults applied
    without adjustment. Removing that divergence between the published
    validation script and the library default is part of what this work did.


Cooling and latent load
=======================

TMHP's indoor coil is a dry sensible-heat exchanger. Manufacturer cooling
grids publish total capacity, part of which is dehumidification, so a cooling
point with a low sensible heat ratio asks the model to carry a latent duty it
does not compute. Each cooling point in the catalogue files records its
sensible heat ratio for this reason. Heating points have no latent component.


Scope
=====

.. admonition:: What has and has not been benchmarked
    :class: note

    ``AirSourceHeatPumpBoiler`` and ``AirSourceHeatPump`` are benchmarked
    against manufacturer catalogues here. The ground-source and water-source
    families (:class:`~tmhp.ground_source_heat_pump_boiler.GroundSourceHeatPumpBoiler`,
    :class:`~tmhp.water_source_heat_pump_boiler.WaterSourceHeatPumpBoiler`,
    :class:`~tmhp.ground_source_heat_pump.GroundSourceHeatPump`, and the
    subsystem-augmented variants) share the same refrigerant-cycle core and
    pass smoke tests on representative operating points, but have not been
    compared against unit-specific data. The conductance rule derived here is
    for air coils and does not transfer to a water- or brine-coupled face,
    which is different physics.

    ``GroundSourceHeatPump`` additionally still carries a fixed compressor
    displacement rather than the derived rule, and is not covered by the
    displacement work on this page.


Reproduce
=========

.. code-block:: bash

    uv sync --locked --group validation
    uv run python -m validation.parity.run             # every catalogue
    uv run python -m scripts.validation.parity_figure  # this figure

Adding a machine is a transcription and nothing else — see
:doc:`adding-a-catalogue`.


Citations
=========

- Jo, H. & Choi, W. *"Thermodynamic Modeling of Refrigerant Cycle in an
  Air-Source Heat Pump Boiler and Performance Validation"*, KJACR (2026, in
  press).
- Samsung Electronics, *EHS Mono HT Quiet R32 Technical Data Book* (2024).
- Panasonic, *Aquarea WH-MXC / WH-WXG / WH-UQ service manuals and compressor
  catalogues* (2025).
- Daikin, *RXM-A engineering data book*, EEDEN24-200.

Full provenance, with checksums, is in ``validation/registry/sources.yaml``.
