======================
Adding a catalogue
======================

This procedure is fixed. Follow it and the documentation site picks the new
unit up on the next push to ``main`` — parity plot, error table and summary,
all of it.

The design goal is that adding a machine should take a transcription and
nothing else. No script gets a new hard-coded unit, no coefficient gets
retuned, and the model that runs against the new grid is the same one every
other unit was run against.


The six steps
=============

.. code-block:: bash

    # 1-2. put the document in validation/evidence/, register it
    uv run python -m validation.extraction.build_registry

    # 3. transcribe the grid to validation/catalogs/<slug>.yaml   (by hand)

    # 4. run the harness
    uv run python -m validation.parity.run --unit <slug>

    # 5. refresh the figure and the site data
    uv run python -m scripts.validation.parity_figure
    uv run python scripts/data/gen_validation_data.py

    # 6. commit the catalogue, the registry, results/ and the regenerated JSON

**1. Archive the document.** Drop it under ``validation/evidence/`` — in
``catalogs/``, ``pdfs/``, ``data/`` or ``screens/``. Nothing there is
committed; publisher PDFs are not redistributable.

**2. Register it.** Add an entry to the ``DESCRIBED`` map in
``validation/extraction/build_registry.py``: title, publisher, document id, the
rating condition it declares, and where a reader can obtain it. Then run the
command above. A document with no entry is listed under ``unused_archive``,
which is the audit trail for "nobody has checked this file yet".

**3. Transcribe the grid.** One YAML file per outdoor unit. See below.

**4. Run the harness.** It writes ``validation/results/<slug>.csv`` and updates
``validation/results/summary.csv``.

**5. Refresh the derived artefacts.** The figure and the site's JSON both read
from ``validation/results/``, so they follow automatically.

**6. Commit.** Pushing to ``main`` rebuilds the site.


The catalogue file
==================

.. code-block:: yaml

    slug: daikin_rxm35a
    name: FTXM35A / RXM35A
    manufacturer: Daikin
    model_class: ASHP              # ASHP (air-to-air) | ASHPB (air-to-water)
    refrigerant: R32
    nominal_capacity_kW: 3.50
    cop_definition: "TC / PI, EN 14511 system power including the indoor fan"
    source:
      document: "Daikin RXM-A engineering data book, EEDEN24-200"
      table: "Capacity tables, cooling and heating at nominal frequency"
    published_inputs:
      rated_indoor_air_flow_m3_s: 0.2067
    points:
      - {id: 1, t_source_C: 35, t_sink_C: 27, q_kW: 3.28, power_kW: 1.04, mode: cooling}
      - {id: 2, t_source_C: 7,  t_sink_C: 20, q_kW: 4.38, power_kW: 0.98, mode: heating}

``model_class``
    ``ASHPB`` means the nameplate is a heating capacity and ``t_sink_C`` is
    leaving water temperature; ``ASHP`` means the nameplate is a cooling
    capacity and ``t_sink_C`` is indoor dry bulb.

``published_inputs``
    Values the manufacturer publishes *about this machine*. Only three keys are
    accepted — ``displacement_cc``, ``rated_air_flow_m3_s`` and
    ``rated_indoor_air_flow_m3_s`` — and the loader raises on anything else.
    These have the same status as the nameplate capacity: specification, not
    tuning. Anything you omit falls back to the library default, which is the
    case :doc:`the derivation work <defaults>` is really about.

``sink_offset_K``
    Air-to-water only: how far the tank temperature sits below the catalogue's
    leaving water temperature. A statement about where the manufacturer
    measures.

``points``
    ``cop`` or ``power_kW``; give either and the other is derived.

.. admonition:: What the schema deliberately cannot express
    :class: important

    There is no field for an efficiency coefficient, a conductance or an
    approach temperature — and there should never be one. The moment a
    catalogue file can carry a free parameter, the parity plot stops meaning
    anything, because every unit can then be made to fit. If a unit cannot be
    matched without adjusting something, that is a finding to report on
    :doc:`the results page <index>`, not a field to add.


Choosing a source
=================

Not every published grid can be used, and the reasons are worth knowing before
spending an afternoon transcribing one.

.. list-table::
    :header-rows: 1
    :widths: 22 78

    * - Source type
      - Verdict
    * - Pair-application split data books
      - **Best.** One outdoor unit, one indoor unit — the structure
        ``AirSourceHeatPump`` models — with no combination-ratio or pipe-length
        correction, and a full temperature grid with power input. Free from the
        manufacturer.
    * - Air-to-water heating tables
      - **Good.** Leaving water temperature against outdoor air, COP printed
        directly. No latent complication.
    * - VRF catalogues
      - **Excluded.** One outdoor unit serves many indoor units, so performance
        is indexed by combination ratio. Mapping that to part load requires an
        assumption about the indoor coil which then sits inside the comparison.
    * - US packaged rooftops
      - **Heating only, and small sizes only.** The cooling tables give a wide
        grid but no power column, so no COP can be formed. Trane's heating
        tables do give total unit power. Above roughly 25 kW the equipment
        architecture changes enough that the comparison stops being meaningful.

Three traps that have already caught this project:

*A grid may be printed twice, in different units.* Fujitsu prints imperial and
then SI; parse the SI block and there is no conversion to get wrong.

*The power column is named differently by every manufacturer* — Daikin ``PI``,
Fujitsu ``IP``, US packaged ``Total Unit Power``. An automatic scan that misses
one will reject a perfectly good source.

*A second grid on the same page may be a different operating point.* Daikin
prints a heating grid at nominal frequency and then one at maximum frequency;
reading past the caption between them mixes two conditions into one set.
Fujitsu's heating grids are entirely at maximum capacity, which is legitimate
but is not a rated condition and should be labelled.

*Check the printed table against the parsed rows, per manufacturer.* Column
order is not consistent even between series of the same maker: Güntner's GHF
prints air flow before surface area and its GACC prints them the other way
round. Every parser under ``validation/extraction/`` asserts one printed row
verbatim before trusting the rest of its table, and the effectiveness check in
the inversion catches most of what slips past that.


Latent load
===========

Cooling grids publish total capacity and sensible capacity; the difference is
dehumidification. TMHP's indoor coil is a dry sensible exchanger, so a row with
a low sensible heat ratio asks the model to carry a latent duty it does not
compute. The Daikin catalogues record each cooling point's sensible heat ratio
in its ``note`` field for exactly this reason. Report such rows; do not quietly
drop them.

Heating rows have no latent component and need no qualification.
