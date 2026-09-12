============================
Where the defaults come from
============================

TMHP decides a lot on your behalf. Hand it a capacity and a refrigerant and it
produces heat-exchanger conductances, a compressor displacement, a rated air
flow and three efficiency correlations. That convenience is the point of the
library — but it is only worth having if each of those numbers can be traced to
a published document rather than to somebody's judgement, and if you can rerun
the step that produced it.

This page is that trace. Every row links to the source and to the script that
reads it.

.. admonition:: The one rule behind all of it
    :class: important

    Published literature reports the conductance, displacement or efficiency of
    *particular machines*. Nobody publishes a rule expressed per unit of
    capacity — which is exactly what a model that sizes its own components
    needs. So the numbers below are not taken from a paper. They are derived
    from populations: manufacturer catalogues that rate an entire product range
    at one declared condition, which makes models of different capacity
    comparable by construction. The citations are for the *method*, the
    *rating standard* and the *correlation*; the numbers are measured across
    many machines.

    A consequence worth stating plainly: validating against a catalogue is
    never calibrating against it. No parameter below is fitted to any unit in
    :doc:`the validation set <index>`.


Summary
=======

.. list-table::
    :header-rows: 1
    :widths: 22 26 32 20

    * - Quantity
      - Default
      - Where it comes from
      - Reproduce
    * - Outdoor coil conductance (air-to-air)
      - ``UA_ou = hp_capacity / 5``
      - 1,414 component coils across two rating standards and six
        manufacturers, converted to the nameplate basis; independently checked
        against 12 heat pumps' published coil geometry
      - ``ua_transfer_law``
    * - Indoor / outdoor conductance ratio
      - ``UA_iu = 0.8 × UA_ou``
      - Derived, not chosen: both faces are air coils, so the ratio is the duty
        ratio ``1 / (1 + 1/EER)``
      - ``ua_transfer_law``
    * - Tank heat-exchanger conductance (air-to-water)
      - ``UA_tank_hx = hp_capacity / 5``
      - 5 K equivalent LMTD; inside the band Deutz et al. (2018) fit for a
        tank-mantle machine
      - —
    * - Evaporator / condenser conductance ratio (air-to-water)
      - ``UA_ou = 0.7 × UA_tank_hx``
      - Duty ratio ``1 − 1/COP``; the measured approach temperatures on the two
        sides are close enough for the transfer to hold
      - ``en328_inversion``
    * - Compressor displacement
      - ``V = Q̇ / (Δh · ρ · η_vol · n_rated)``
      - Pure fluid property from CoolProp at a declared rating point; rated
        speed inverted from nine machines with published displacement
      - ``compressor_speed``
    * - Isentropic efficiency
      - ``0.90 − 0.02 · PR``
      - Standard semi-empirical form; no speed term, because the two sources
        that report one disagree in sign
      - —
    * - Volumetric efficiency
      - ``1 − 0.020 (PR − 1) − k (1/n − 1/50)``
      - Clearance re-expansion plus internal leakage; leakage sized from Cuevas
        & Lebrun's measured 50 → 35 Hz loss
      - —
    * - Electro-mechanical efficiency
      - ``0.80 × shape(n / n_rated)``
      - Guth & Atakan's measured shape, read in relative speed; cross-checked
        against 185 inverter measurements
      - ``compressor_speed_losses``
    * - Rated outdoor air flow
      - 720 m³/h per kW (air-to-air), 540 (air-to-water)
      - Manufacturer specifications for the respective product classes
      - —

Every ``Reproduce`` entry is a module under ``validation/extraction/``::

    uv run python -m validation.extraction.<name>


Heat-exchanger conductance
==========================

The problem
-----------

``AirSourceHeatPump`` generates both coil conductances from a single declared
capacity. What has to be established is therefore a capacity-normalised rule
and a ratio — not any particular machine's conductance. Before this work the
code carried ``hp_capacity / 10`` and a ratio of ``0.8`` with no citation
anywhere, in the source or in the manuscript.

Why catalogue inversion is not a fit
------------------------------------

When the refrigerant side of a coil changes phase its capacity rate is
unbounded, the capacity-rate ratio vanishes, and the LMTD and
effectiveness-NTU descriptions of the coil become the same equation:

.. math::

    UA = -\ln\!\left(1 - \frac{\dot Q}{C_{\mathrm{air}}\,\Delta T_1}\right) C_{\mathrm{air}}

A catalogue that declares duty, air volume flow and a rating temperature
difference therefore pins the conductance exactly. There is no parameter to
choose, so the result is either right or the table was misread — and a misread
column almost always pushes the effectiveness outside ``(0, 1)``, which the
code refuses. ``validation.extraction.dt_convention`` checks the identity
numerically over NTU 0.2–5.0.

This also settles a question that looks like it needs a convention: it does
not. ``UA/Q`` is the same number whether you compute it on an LMTD or an
inlet-end basis. Only the *outlet-end approach temperature* differs, and that
converts exactly through the NTU. All conductances here are reported as an
equivalent LMTD, which is why they can be compared across sources at all.

What the catalogues say
-----------------------

.. list-table::
    :header-rows: 1
    :widths: 16 12 34 12 13 13

    * - Rating standard
      - Models
      - Manufacturers
      - Air flow [m³/h per kW]
      - ``UA/Q`` median
      - Equivalent LMTD
    * - EN 328 SC2 (evaporators)
      - 352
      - Alfa Laval · Güntner ×2 · GEA Searle
      - 584
      - 0.190
      - 5.28 K
    * - ENV 327 (condensers)
      - 1,062
      - LU-VE · Alfa Laval
      - 240
      - 0.146
      - 6.86 K

Two standards written by different committees for different applications,
adopting air flows per kilowatt that differ by a factor of 2.4, place their
product populations in the same band of conductance per unit duty. That
agreement *between* the standards is the evidence. A regression *through* them
would mean nothing: inside a single standard ``UA/Q`` and air flow are linked
by an identity rather than a trend, so the points lie on a curve and a line
fitted through two such curves is an artefact.

.. admonition:: A claim withdrawn
    :class: caution

    An earlier version of this work reported a pooled regression of ``UA/Q``
    against air flow — slope −0.020, R² 0.001 — as evidence that the two are
    unrelated. That was wrong for the reason just given, and it is withdrawn.
    What survives is that the two bands overlap, which is circumstantial. The
    proof is the geometric route below, which is bound by no rating identity at
    all and lands in the same place.

From the band to the default
----------------------------

The nameplate is the *indoor* coil's cooling duty; the outdoor coil rejects
that duty plus the compressor work. A rule written against the nameplate has to
carry ``1 + 1/EER`` explicitly — 1.29 to 1.35 across the reference units.
Skipping it misses by about 30 %. Applying it to the condenser band gives
``UA/Q_cool = 0.19``, that is ``hp_capacity / 5``.

Adding the second condenser manufacturer late in this work was a useful test of
how settled that is: it moved the condenser median by 5 % and the rounded
default not at all.

The independent check
---------------------

Trane prints, for every Precedent packaged heat pump, the outdoor coil's face
area, row count, fin density and tube size *together with* the outdoor fan flow
and the rated capacity. That is enough to compute the conductance directly from
a published air-side correlation — `Wang, Chi & Chang (2000)
<https://doi.org/10.1016/S0017-9310(99)00333-6>`_, already cited by the
manuscript — with Schmidt fin efficiency, without reference to any component
catalogue. The two routes share neither inputs nor method.

Over twelve units of two refrigerant generations the geometric route gives
``UA/Q_cool`` between 0.119 and 0.206, a median of ``Q/6.8``, and implied
condensing temperatures of 47–55 °C at the 35 °C rating point, which is the
product class's ordinary range. Its largest declared quantity is the
refrigerant-side film coefficient: at 2500 W/(m² K) the route gives ``Q/6.8``,
and with that resistance removed entirely it gives ``Q/4.3``. The band result
sits between the two.

That is why the band route sets the default: it needs no assumption about the
refrigerant side at all. The geometric route is the corroboration, and
``validation.extraction.trane_geometry`` prints the sensitivity of its answer
to every quantity it had to declare rather than read.

.. admonition:: How wide the band is, and what that costs you
    :class: warning

    ``Q/5`` is a *median*. The population it came from spans ``UA/Q`` 0.108 to
    0.230 at p10–p90 — about ``Q/4`` to ``Q/9`` — and that width is real
    hardware, not measurement noise. The validation set shows what it costs:
    backing the conductance out of each machine's own rating point, a
    high-efficiency Daikin split implies roughly ``Q/4.5`` and a budget Fujitsu
    split roughly ``Q/7``, and their nameplate efficiencies differ by 42 %.

    So a single vendor-neutral default describes a typical machine near the
    efficient end of current practice, and **will over-predict a
    lower-efficiency product line by something like 30 %**. If you know the
    machine, pass ``UA_ou_rated``. See :doc:`index` for the measured spread.

.. admonition:: Where this does not apply at all
    :class: warning

    The rule is established for dry, round-tube-plate-fin outdoor coils of
    packaged equipment. Frosted operation, microchannel coils and the
    residential mini-split product class lie outside the evidence. The
    mini-split exclusion matters in practice: TMHP's rated air-flow default of
    720 m³/h per kW is a mini-split figure, while the geometric evidence comes
    from packaged units at roughly half that. The two defaults presently point
    at different product classes, and that is an open item rather than a
    resolved one.


Compressor displacement
=======================

The machine has to move enough refrigerant to carry the nameplate duty at the
nameplate condition:

.. math::

    V_{\mathrm{disp}}
        = \frac{\dot Q_{\mathrm{nom}}}
               {\Delta h \; \rho_{1} \; \eta_{\mathrm{vol}} \; n_{\mathrm{rated}}}

Both :math:`\Delta h` and :math:`\rho_1` come from CoolProp at a declared
rating point, so the dependence on the working fluid is pure fluid property
rather than a tabulated coefficient. That dependence is not small: per kilowatt
of nameplate the displacement runs from about 3.9 cm³/rev for R32 to 10.4 for
R1234yf. The constant this replaced took no account of the fluid at all.

The rating point is declared per equipment class — EN 14511 A7/W35 for
air-to-water, ISO 5151 T1 for air-to-air — and the rated speed is the one
quantity that carries real uncertainty, since the result is exactly inversely
proportional to it.

For air-to-water it is not a guess. Inverting the relation against the nine
Panasonic Aquarea units whose compressor displacement is published by the
manufacturer's compressor division gives a median rated speed of 42 rev/s, and

.. list-table::
    :header-rows: 1
    :widths: 20 26 26 28

    * - Nameplate
      - R32
      - R290
      - R410A
    * - 9 kW
      - 33.6 rev/s
      - 32.8
      - 36.2
    * - 12 kW
      - 44.9
      - 43.7
      - 48.3

— at a fixed capacity the three refrigerants agree within 5 %. The physics
carries the fluid dependence on its own. The scatter that remains is the
manufacturers' practice of sharing one compressor across adjacent capacity
steps: in all three of those product lines the 9 and 12 kW units use the same
machine, which no continuous rule can reproduce.

For air-to-air the evidence is thinner. Daikin's SL-series service manual
publishes rated compressor frequency in place of displacement — 52 rev/s
cooling for the 2.5 kW class, 72 for the 3.5 kW class — and no displacement is
published anywhere to check the result against. A search of five data books,
three service manuals and two specification sheets, about 90 MB, returned no
displacement for any Daikin, Fujitsu or Trane unit; every ``cm³`` in those
documents is an oil charge. This is the weakest link in the chain and is
treated as such.

.. tip::

    A displacement error is second order for steady-state COP: the speed solver
    absorbs it into speed, and the result moves only through the speed
    dependence of the efficiencies. Where it matters is the modulation envelope
    — displacement and ``rps_min`` jointly fix the lowest duty the machine can
    deliver. If you know your machine's displacement, pass ``V_cmp_ref``.


Compressor efficiency
=====================

A variable-speed compressor loses work in three distinguishable places, and
TMHP keeps them separate because the evidence for each behaves differently.
The correlations live in :mod:`tmhp.compressor_efficiency` and are shared by
every model, so the library default and the published validation script can no
longer drift apart — which they previously had.

Where the low-speed loss belongs
--------------------------------

Not all on one coefficient. `Cuevas & Lebrun (2009)
<https://doi.org/10.1016/j.applthermaleng.2008.03.016>`_ ran the controlled
contrast: at 35 Hz both the isentropic and the volumetric effectiveness
degrade, while at 75 Hz only the isentropic one does. They read the first as
internal leakage and the second as mechanical loss. A single speed-dependent
factor cannot express that.

So the volumetric correlation carries a leakage term in ``1/n`` — leakage is
driven by pressure difference and is nearly independent of speed, while swept
mass flow is proportional to it, so the fraction lost goes as one over the
speed. Its size is set by the roughly three percentage points Cuevas & Lebrun
measure between 50 and 35 Hz. Two caveats travel with it: one machine, and its
own authors read the effect as oil starvation rather than as an intrinsic
property of scroll compressors.

The isentropic correlation carries no speed term at all. `Guth & Atakan (2023)
<https://doi.org/10.1016/j.ijrefrig.2022.10.024>`_ report one, but it runs the
opposite way to intuition — their isentropic efficiency is *higher* at low
speed — while Cuevas & Lebrun see mild degradation at both ends. The two
disagree in sign, so neither is carried.

The electro-mechanical shape
----------------------------

Guth & Atakan's Table A.3 gives a measured combined mechanical, electrical and
drive efficiency as a function of pressure ratio, speed and evaporating
temperature. It peaks near 70 rev/s and falls away on both sides: fixed
magnetising and friction losses dominate below, flow and windage losses above.

That shape is read in **relative** speed, ``n / n_rated``, and normalised to 1
at the rated point. Anchoring it at an absolute speed would be the wrong
transfer — a motor and its drive are wound and sized for the speed the
compressor is rated at, so an air-to-water unit rated near 40 rev/s would
otherwise sit permanently on the falling side of a curve measured on a machine
rated near 70. What a single machine's measurement can reasonably supply is the
*curvature*: how fast efficiency falls away from the design speed.

.. admonition:: This mattered, and the order matters too
    :class: note

    Anchored at an absolute 50 rev/s, the measured shape made the catalogue fit
    **worse**: across the ten air-to-water units, 10.0 % weighted MAPE against
    8.8 % for the unsourced parabola it replaced. Read in relative speed the
    same set gives 7.4 %, the best of the three.

    The order matters. The physical argument — that a drive is sized for its own
    machine's rated speed — came first, and the error followed it. Had the
    numbers gone the other way they would be reported here just the same.

The relative shape is cross-checked against a second, independent source.
`Ossorio & Navarro-Peris (2023)
<https://doi.org/10.1016/j.applthermaleng.2023.120725>`_ publish drive
efficiency against output frequency for three inverters over 15–110 Hz — 185
measured points, and the only source in this evidence base that goes below
30 Hz. Fitting ``η = η_max · f/(f + f₀)`` to them gives a 6–11 % drop from 50 to
15 rev/s for the drive alone. The combined shape gives about 8 %, which is
consistent and correctly larger, since it also carries the motor and the
bearings.

What is deliberately absent
---------------------------

No low-load cliff. See :doc:`part-load` for why inventing one would contradict
the measurements, and for what TMHP does and does not model at light load.


Provenance
==========

The source documents are archived locally but **not** committed: publisher PDFs
and supplementary datasets are not redistributable. The tracked half is
``validation/registry/sources.yaml``, which records what each document is,
where to obtain it and the SHA-256 of the copy the scripts were run against. A
reader who obtains the same document from the manufacturer and gets the same
checksum is running on identical bytes, and everything under
``validation/data/`` follows from those bytes by the scripts in
``validation/extraction/``.

.. seealso::

    :doc:`index`
        Catalogue parity results, unit by unit.

    :doc:`part-load`
        The part-load trend, and the certified data it is judged against.

    :doc:`adding-a-catalogue`
        How to add a unit and have all of it update.
