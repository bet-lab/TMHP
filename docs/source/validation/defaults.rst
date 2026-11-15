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
      - ``V = Q̇ / (ρ₁ · Δh · η_vol·n)`` with ``η_vol·n`` = 53.2 rev/s, so it
        follows the refrigerant (``estimate_displacement``). The ground- and
        water-source boilers keep 42 cm³/rev per 9 kW
      - 41 catalogue rows of inverter twin-rotary compressors (LG, Mitsubishi
        Electric SCI; 14 families), pooled by dividing out ``ρ₁ · Δh`` at each
        row's own rating point, then evaluated at that rating point
        (7.2 / 54.4 °C)
      - ``default_inputs.compressor_displacement``
    * - Compressor efficiencies (volumetric, isentropic, electro-mechanical)
      - Coefficients ``BITZER-three-C3-absolute-N-2026-10-06``: each
        ``b0 + b1·PR + b2·N + b3·PR² + b4·N² + b5·PR·N`` with ``N`` the shaft
        speed in rev/s
      - Three independent quadratic fits to BITZER scroll calculation data
        (9 machines, 3,899 points, superheat/subcooling 10/0 K)
      - ``validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/``
    * - Efficiency guards
      - ``0 < η ≤ 1``; fitted box PR 1.50–7.15, N 33.8–72.5 rev/s
      - Above one is set to one, nonpositive is infeasible. Outside the box:
        held at the boundary towards high PR and high N, continued along the
        boundary slope towards low PR (to 1) and low N (to 15 rev/s) only where
        that lowers the efficiency
      - :mod:`tmhp.compressor_efficiency`
    * - Pressure-ratio limit
      - ``PR_cycle_max`` 11 (air-to-air), 11 (air-to-water)
      - Operating points above it are rejected. 11 is the single-stage
        scroll self-unload limit, above the ratio near 7 a split unit reaches
        lifting from −15 °C outdoor air. Higher ratios are two-stage or
        injection machines; the discharge-temperature limit (R32 150 °C,
        other refrigerants 135 °C, held first by suction control) binds
        before it almost everywhere
      - :mod:`tmhp.compressor_envelope`
    * - Rated outdoor air flow
      - Air-to-air: ``0.1084·Q + 0.1776`` m³/s (``Q`` in kW, cooling-rated).
        Air-to-water: ``0.0864·Q + 0.2208`` m³/s (heating-rated)
      - Largest printed fan step of 128 single-split outdoor units (31
        families, four manufacturers) and of 59 air-to-water outdoor units
        (13 families, 6 manufacturers; the widest scatter of the airflow
        laws, see `Maximum fan airflow`_)
      - ``default_inputs.fan_airflow``
    * - Rated indoor air flow
      - Wall-mounted split: ``0.0320·Q + 0.1035`` m³/s. Water-to-air
        (ground source): ``0.0491·Q + 0.1305`` m³/s (cooling-rated)
      - Largest printed fan step of 130 wall-mounted units (38 families, five
        manufacturers) and 52 water-to-air units (7 families, four
        manufacturers). The validation units publish their own indoor airflow
        and are run at it, so the indoor default does not enter the parity
        results
      - ``default_inputs.fan_airflow``

Every ``Reproduce`` entry is a module under ``validation/extraction/``,
``validation/compressor_maps/`` or ``validation/default_inputs/``::

    uv run python -m validation.extraction.<name>
    uv run python -m validation.compressor_maps.<name>
    uv run python -m validation.default_inputs.<name>.build

The shipped compressor coefficients are version
``BITZER-three-C3-absolute-N-2026-10-06``
(:data:`tmhp.compressor_efficiency.COEFFICIENT_VERSION`), from the
`BITZER three-efficiency study <https://app.notion.com/p/3ee6947d125d80f4830ffcb408fa647d>`_
of 2026-10-06; ``validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/``
holds the coefficients and the fitted surfaces. The previous correlations,
version ``v2026-09-24``, remain in :mod:`tmhp.compressor_efficiency_legacy`, and
``validation/coefficients/v2026-09-24/`` holds their data list, fits,
cross-validation, selection table and isentropic/electro-mechanical split.
The `Compressor efficiency`_ section below documents how those earlier
correlations were derived; it is the record of that work, not a description of
what ships.


Heat-exchanger conductance
==========================

The problem
-----------

``AirSourceHeatPump`` generates both coil conductances from a single declared
capacity. What has to be established is therefore a capacity-normalised rule
and a ratio — not any particular machine's conductance. Before this work the
code carried a divisor of 10 and a ratio of ``0.8`` with no citation anywhere,
in the source or in the manuscript. The derived rule below replaced the
divisor; a merge on 2026-10-06 put the uncited value back by accident, and the
derived rule was restored on 2026-11-12.

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

    ``Q/5`` is a *median*. On the nameplate basis the population it came from
    spans **``Q/3.3`` to ``Q/7.1``** at p10–p90, and that width is real
    hardware, not measurement noise.

    The validation set shows what it costs. Backing the conductance out of each
    machine's own cooling rating point with the shipped defaults, the two
    largest Daikin splits imply ``Q/3.8`` and ``Q/5.6`` — inside — while the
    three smaller ones need more conductance than the band's high end (``Q/2.6``
    for the 3.5 kW unit; none in ``Q/2``–``Q/16`` closes the gap for the 2.0 and
    2.5 kW units), and the two Fujitsu splits imply ``Q/7.1`` and ``Q/8.7``, at
    and beyond the low-conductance end. Their nameplate efficiencies differ
    accordingly: EER 5.41 down to 3.23 across the seven.

    So a single vendor-neutral default describes a typical machine, and **will
    under-predict a high-efficiency product line and over-predict a
    lower-efficiency one**. If you know the machine, pass
    ``UA_ou_rated``. :doc:`index` decomposes the rest of that residual, which
    is not all conductance.

    .. note::

        Quote the band on the same basis as the rule. Component catalogues
        report conductance per unit of *coil* duty (``UA/Q`` 0.108–0.230); the
        rule is written against the *nameplate*, and the two differ by
        ``1 + 1/EER`` = 1.307. An earlier version of this page carried the
        median across but not the band.

.. admonition:: Where this does not apply at all
    :class: warning

    The rule is established for dry, round-tube-plate-fin outdoor coils of
    packaged equipment. Frosted operation, microchannel coils and the
    residential mini-split product class lie outside the evidence. The
    mini-split exclusion matters in practice: TMHP's rated air-flow default is
    the catalogue maximum of single-split units (573 m³/h per kW at 3.5 kW,
    470 at 8 kW; `Maximum fan airflow`_), while the geometric evidence comes
    from packaged units at roughly 360. The two defaults still point at
    different product classes, and that is an open item rather than a
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
of nameplate the displacement runs from about 3.0 cm³/rev for R32 to 7.3 for
R134a.

What is left, :math:`\eta_{\mathrm{vol}}\, n`, belongs to the compressor, and
compressor catalogues print it. Each row gives a displacement and a capacity at
a declared rating point, so
:math:`\eta_{\mathrm{vol}}\, n = \dot Q / (V \rho_1 \Delta h)` row by row.
Across 41 rows of inverter twin-rotary compressors from two manufacturers (LG
and Mitsubishi Electric SCI; 14 families, R32 and R410A, 2.9 to 14.9 kW) it is
53.2 rev/s — 0.887 of the 60 rev/s the rows are published at. Predicting a
family, a manufacturer or a refrigerant from the others misses the printed
displacement by 2.7, 2.6 and 3.8 %.

The default displacement is that relation at the catalogues' own rating point
(evaporating 7.2 °C, condensing 54.4 °C, suction 18.3 °C, liquid 46.1 °C), with
the nameplate capacity standing in for the compressor's catalogue capacity
(:func:`tmhp.default_inputs.estimate_displacement`). ``AirSourceHeatPump``,
``GroundSourceHeatPump`` and ``AirSourceHeatPumpBoiler`` use it when
``V_cmp_ref`` is left out, and say so: ``model.default_input_estimates["V_cmp_ref"]``
holds the value, the data release, the capacity range of the catalogue rows,
whether the capacity lies outside that range (``extrapolated``) and the source
documents. A ``V_cmp_ref`` you pass is used as given and has no entry there.

.. list-table:: Default displacement [cm³/rev]
    :header-rows: 1
    :widths: 16 24 15 15 15 15

    * - Nameplate
      - Until November 2026 (any refrigerant)
      - R32
      - R410A
      - R290
      - R134a
    * - 3.5 kW
      - 16.33
      - 10.55
      - 11.64
      - 19.68
      - 25.60
    * - 8 kW
      - 37.33
      - 24.10
      - 26.61
      - 44.98
      - 58.51
    * - 15 kW
      - 70.00
      - 45.20
      - 49.90
      - 84.34
      - 109.70

The earlier default, 42 cm³/rev per 9 kW for every refrigerant, was 55 % above
the catalogue value for R32 and 17 % and 36 % below it for R290 and R134a. The
speed solver absorbed the difference: an R134a boiler was rated at 89.8 rev/s,
above the 72.5 rev/s the efficiency fits reach, and an R32 split at 31.2 rev/s,
below the 33.8 rev/s they start at. With the catalogue displacement the rated
speed of every default machine lies inside the fitted range.
``GroundSourceHeatPumpBoiler`` and ``WaterSourceHeatPumpBoiler`` still use the
earlier constant (:func:`tmhp.compressor_speed.default_displacement`).

The class rating points
-----------------------

:func:`tmhp.compressor_speed.refrigerant_aware_displacement` evaluates the same
relation at any declared rating point and rated speed. Two are declared per
equipment class — EN 14511 A7/W35 for air-to-water, ISO 5151 T1 for
air-to-air — and their rated speed is the one quantity that carries real
uncertainty, since the result is exactly inversely proportional to it. They are
not what the default uses; they are the record of how the rated speed of each
class was bounded before compressor catalogues were collected.

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


Maximum fan airflow
===================

A fan default has to say *which* airflow it is. Catalogues print several fan
steps, and the one the model needs is the largest: the rated airflow is where
the fan stops (``dV_*_fan_a_max`` defaults to it), where the coil conductance
is stated and where the fan power is anchored. So the default is the largest
fan step a product publishes, fitted against the capacity of the same duty —
cooling airflow against cooling capacity, heating against heating — with one
law per product class. Families are weighted equally, and of a proportional, a
linear and a power law the simplest within 2 % of the best family-hold-out
error is kept (:func:`tmhp.default_inputs.estimate_max_airflow`; ``Q`` is the
capacity in kW, the result m³/s).

.. list-table:: Catalogue airflow laws the constructors use
    :header-rows: 1
    :widths: 34 22 16 14 14

    * - Input left out
      - Law [m³/s]
      - Rows / families / manufacturers
      - Capacity [kW]
      - Family hold-out MAPE
    * - ``AirSourceHeatPump`` outdoor, cooling-rated
      - ``0.1084·Q + 0.1776``
      - 128 / 31 / 4
      - 1.5–13.6
      - 14.5 %
    * - ``AirSourceHeatPump`` outdoor, heating-rated
      - ``0.1224·Q``
      - 125 / 30 / 4
      - 2.0–16.0
      - 16.4 %
    * - ``AirSourceHeatPump`` indoor wall-mounted, cooling-rated
      - ``0.0320·Q + 0.1035``
      - 130 / 38 / 5
      - 1.5–9.7
      - 12.9 %
    * - ``AirSourceHeatPump`` indoor wall-mounted, heating-rated
      - ``0.0312·Q + 0.0950``
      - 131 / 37 / 5
      - 2.0–11.2
      - 12.4 %
    * - ``AirSourceHeatPump`` indoor floor-standing, cooling-rated
      - ``0.0425·Q``
      - 26 / 7 / 3
      - 2.4–13.6
      - 12.8 %
    * - ``AirSourceHeatPump`` indoor floor-standing, heating-rated
      - ``0.0330·Q + 0.0402``
      - 26 / 7 / 3
      - 3.2–16.0
      - 10.9 %
    * - ``GroundSourceHeatPump`` indoor, cooling-rated
      - ``0.0491·Q + 0.1305``
      - 52 / 7 / 4
      - 4.7–21.6
      - 9.6 %
    * - ``GroundSourceHeatPump`` indoor, heating-rated
      - ``0.1158·Q^0.824``
      - 52 / 7 / 4
      - 3.4–16.9
      - 8.2 %
    * - ``AirSourceHeatPumpBoiler`` outdoor, heating-rated
      - ``0.0864·Q + 0.2208``
      - 59 / 13 / 6
      - 3.0–16.0
      - 20.2 %

The duty is the one the nameplate names: the mode of the rating condition
(cooling unless ``rated_condition={"mode": "heating"}`` is given).
``indoor_unit_type="floor_standing"`` selects that class for
``AirSourceHeatPump``; cassette and ducted units have no stratum and need
their own ``dV_iu_fan_a_rated``. A capacity outside the catalogue rows still
gets a value, flagged ``extrapolated`` in
``model.default_input_estimates["dV_iu_fan_a_rated"]`` (and
``["dV_ou_fan_a_rated"]``) — the default 4 kW ``GroundSourceHeatPump`` is such
a case, below the smallest water-to-air row.

.. list-table:: Default rated (= maximum) airflow [m³/s], before → after
    :header-rows: 1
    :widths: 40 20 20 20

    * - Input
      - 3.5 kW
      - 8 kW
      - 15 kW
    * - ``AirSourceHeatPump`` ``dV_ou_fan_a_rated``
      - 0.700 → 0.557
      - 1.600 → 1.045
      - 3.000 → 1.804
    * - ``AirSourceHeatPump`` ``dV_iu_fan_a_rated``
      - 0.188 → 0.216
      - 0.430 → 0.360
      - 0.805 → 0.584
    * - ``GroundSourceHeatPump`` ``dV_iu_fan_a_rated``
      - 0.188 → 0.302
      - 0.430 → 0.523
      - 0.805 → 0.867
    * - ``AirSourceHeatPumpBoiler`` ``dV_fan_a_rated``
      - 0.525 → 0.523
      - 1.200 → 0.912
      - 2.250 → 1.517

Until November 2026 the outdoor airflow was ``0.0002 m³/s`` per watt
(720 m³/h per kW) for air-to-air and ``0.00015 m³/s`` per watt (540 m³/h per
kW) for air-to-water, and both indoor airflows ``5.37e-5 m³/s`` per watt
(400 cfm per ton, the airflow unitary equipment is *rated* at, not its
largest). Against the catalogue maxima the air-to-air outdoor figure was 1.26
to 1.66 times too large, the air-to-water one 1.00 to 1.48 times, and the
water-to-air indoor figure 0.62 to 0.93 of it.

.. admonition:: Where the catalogue airflow meets the dry-coil model
    :class: note

    The indoor laws give 150 to 160 m³/h per kW for wall-mounted units of
    9 kW and above and for floor-standing units of any size. TMHP's coils are
    dry, so the whole nameplate is sensible heat, and at that airflow the room
    air has to change by about 20 K: the refrigerant then stands 20 to 25 K
    from the room air at nameplate capacity (a 15 kW unit condenses at 42 °C
    in a 20 °C room and evaporates at 4.6 °C in a 27 °C one).

    That is why the approach-temperature search box of ``AirSourceHeatPump``
    (``dT_approach_bounds``) is 1 to 30 K — the box the catalogue parity runs
    have always used. With the earlier upper bound of 20 K the box itself was
    the limit at the rating point: a default unit reached its nameplate at
    35/27 °C only up to 8 kW (98 % at 9 kW, 89 % at 15 kW; a floor-standing
    unit 96 % at any capacity), and in heating at −7 °C outdoors fell short
    from 6 kW (86 % at 8 kW). At 30 K every default unit from 1.5 to 50 kW,
    of either indoor class and either rated duty, delivers its nameplate at
    both rating points. In mild weather the ceiling of the default 4 kW unit
    is then its compressor (164 % of the nameplate at 7/20 °C, 145 % at
    35/27 °C) rather than the box (126 % and 124 % at 20 K).

The air-to-water outdoor stratum is the weakest of the airflow laws and says
so. It has 59 rows from 13 families and 6 manufacturers (Daikin, LG,
Mitsubishi Electric, Samsung, Toshiba, Vaillant), and the manufacturers
differ: predicting each from the other five misses by 22.7 % on average,
between −27 % and +25 % by manufacturer, and a family held out by 20.2 % —
both a little over the 20 % the other strata were held to. It ships as the
default by the owner's decision (2026-11-15), as a central value with that
spread stated: a default is chosen by the evidence for the input, and against
the nine Panasonic units of the validation set — which are not in the stratum
— the law misses the published airflow by 14.1 % (8.0 % low on average) where
the constant missed it by 32.5 %.
``model.default_input_estimates["dV_fan_a_rated"]`` carries the sources, the
hold-out errors and the spread between manufacturers (a unit's own airflow
can differ from the default by about ±23 %); a published airflow passed as
``dV_fan_a_rated`` replaces it.


Compressor efficiency
=====================

.. admonition:: This section is the record of the ``v2026-09-24`` correlations
    :class: note

    The correlations derived below shipped from 2026-09-24 to 2026-10-06 and
    remain in :mod:`tmhp.compressor_efficiency_legacy`. TMHP now ships the
    BITZER fits listed in the `Summary`_, with the range and validity-domain
    guards described there. The method, data and selection rules below are
    kept because the comparison in the next section still uses them.

A variable-speed compressor loses work in three distinguishable places, and
TMHP keeps them separate because they act on different outputs: the isentropic
efficiency sets the discharge enthalpy (and so the heating duty), the volumetric
efficiency sets the mass flow, the electro-mechanical efficiency sets the
electrical input. The correlations live in :mod:`tmhp.compressor_efficiency`
and are shared by every model.

Compressor data, not heat-pump data
-----------------------------------

The coefficients are fitted to *standalone compressor* performance and then
frozen. Heat-pump catalogue COP is never used to fit them — a catalogue COP
mixes compressor, conductance, fan and latent effects, and fitting the
compressor to it would let a conductance error hide inside an efficiency
coefficient. The catalogue set is what the assembled model is checked against
afterwards (:doc:`index`).

.. list-table::
    :header-rows: 1
    :widths: 30 14 14 42

    * - Source
      - Machines
      - Speed records
      - What it gives
    * - Copeland Online Product Information — AHRI 540 coefficient sets of the
        ZPV / XPV / YPV / ZHV / YHV variable-speed scrolls
      - 63
      - 96
      - Capacity, power and mass-flow polynomials at two or three rated speeds
        per machine (``n*`` 0.27–1.3), R-410A, R-32, R-407C; dew-point
        rating, 20 °F superheat, 15 °F subcooling, power at the drive input
    * - `Cuevas & Lebrun (2009) <https://doi.org/10.1016/j.applthermaleng.2008.03.016>`_
      - 1
      - 5
      - 48 calorimeter tests of an R-134a scroll, 35–75 Hz, with measured
        discharge temperature — the only rows that split the product under
        TMHP's own definition
    * - `Guth & Atakan (2023) <https://doi.org/10.1016/j.ijrefrig.2022.10.024>`_
      - 1
      - 4
      - Published efficiency functions of an R-290 scroll (ZHV046), evaluated
        on their fitting envelope, 1800–5400 rpm
    * - Highly rotary catalogue 2024
      - 8
      - 1
      - R-290 inverter rotaries at their ASHRAE/T rated point, 3600 rpm — a
        level anchor for the rotary type, no speed information
    * - `Shao et al. (2004) <https://doi.org/10.1016/j.ijrefrig.2004.02.008>`_
      - 3
      - 18
      - Manufacturer map polynomials of three inverter rotaries (Mitsubishi,
        SANYO, Hitachi; R-22 inferred) at 30–120 Hz, ``n*`` 0.4–2.0 — the
        only rows above 1.5× rated speed; two of the three fail the
        displacement check and enter the product fit only

Every point is reduced to the same three quantities with CoolProp at the
source's own rating convention: ``eta_vol = ṁ / (ρ_suc V n)``,
``eta_oi = ṁ Δh_is / P_el`` and, where a discharge temperature is printed,
``eta_isen = Δh_is / (h_dis − h_suc)``. Fixed-speed machines, grid points
outside a 15–60 K lift or a 1.5–8 pressure ratio, and machines whose printed
displacement fails a rated-point volumetric check are excluded and listed in
the archive.

What the data identify
----------------------

Power tables identify the *product* ``eta_isen · eta_em``, not its factors.
Within each machine the low-speed loss of the product grows with the pressure
ratio -- about −9 % at PR 2 and −30 % at PR 4.5 at a quarter of rated speed
across the Copeland set -- the signature of internal leakage, whose fraction
goes as the pressure difference over the speed. The product is therefore
fitted as ``g(PR) · (1 − c (PR − 1) max(0, 1/n* − 1))`` with the shape
identified from within-machine contrasts (fixed-effects estimator), because two
thirds of the rows sit at rated speed and the between-machine level spread
would otherwise dilute every speed term. The split into the two factors is set
from the Cuevas & Lebrun rows with measured discharge temperature (29
inverter-fed rows, n* 0.7–1.5): ``eta_em = 0.941 · s(n*) · m(PR)`` at PR 3 and
n* 1, where the drive term ``s`` is anchored on the drive-only floor of the
three Ossorio & Navarro-Peris inverters (``n0 = 0.026``; the one-machine total
of 0.075 over-states the population's low-speed loss by a factor 2.7 in the
within-machine check) and ``m(PR)`` is the motor-load term. Whatever the
product demands beyond that lands in the isentropic efficiency.

The speed penalty measured across the Copeland set does grow with pressure
ratio (the interaction term is statistically significant across 49 machines).
Carrying it would need a speed term in the isentropic efficiency; it buys
0.05 pp of cross-validated error once the rotary maps are in and is kept as a
documented extension, not adopted. The correlations only become more complex
when the data demand it clearly, and 0.05 pp against a between-machine spread
of 7 pp is not that.

A speed term must also be *identified* by the machines that carry it. The
three 2004 rotaries are the only maps above 1.5× rated speed, and their whole
level sits 27 % below the population; pooled, that level shift looks like a
high-speed roll-off (a fitted 28 % at twice rated speed, and 0.8 pp of
cross-validated gain). Inside each of those machines the trend is a tenth of
the fitted magnitude. Rule R4 therefore compares every added speed term with
the within-machine trend of the machines that see it and accepts the term only
if they show at least two thirds of it; the roll-off fails (0.12), the low-speed
drive loss passes (2.2 — the machines show it more strongly than the pooled fit
does). The form keeps a zero-valued roll-off coefficient so a second
high-speed source can switch it on.

Selection
---------

Five volumetric and fifteen product families were compared by
leave-one-compressor-out cross-validation: refit without each machine, predict
it, pool the error weighted so every compressor × speed record counts once.
A family is accepted over its simpler parent only if each extra coefficient
buys at least 0.1 pp of cross-validated MAPE, no stratum with three or more
machines gets worse by more than 2 pp or 25 %, the delivered duty
``n · eta_vol`` stays monotonic in speed on the whole grid, and any added
speed term is seen within the machines that identify it (above).

.. list-table::
    :header-rows: 1
    :widths: 22 44 17 17

    * - Efficiency
      - Adopted form
      - LOCO MAPE
      - Pre-refit (v1)
    * - Volumetric
      - ``1 − 0.0260 (PR − 1) − 0.0223 max(0, 1/n* − 1)``
      - 5.95 %
      - 5.88 %
    * - Product ``eta_isen · eta_em``
      - ``(1.1660 − 0.0832 PR − 0.7017/PR) · (1 − 0.0237 (PR − 1) max(0, 1/n* − 1))``
      - 11.72 %
      - 12.21 %

The product's pressure-ratio shape peaks near ``PR = sqrt(C/B) ≈ 2.8`` — the
built-in volume ratio of an air-conditioning scroll — and falls on both sides:
under-compression below, over-compression and leakage above. Its speed factor
is the saturating drive-loss form Ossorio & Navarro-Peris fit to 185 inverter
measurements, here with the time constant fitted on the whole set; an
exponential alternative scored within 0.06 pp (inside the 0.1 pp resolution
the acceptance rule itself uses) and the form with the physical precedent was
kept. The pooled error of the product is dominated by the three R-22 rotaries
(32 % in their stratum against 9 % for the scrolls): their level, not their
shape, is what the population does not share. The volumetric speed term is one-sided (no bonus above
rated speed): the Copeland set loses about 5 points of volumetric efficiency
at a quarter of rated speed, a third of what the pre-refit leakage term had
extrapolated from one machine.

Speed is read relative to the machine's rated speed, ``n* = n / n_rated``: a
drive and motor are sized for the speed the compressor is rated at, and a
shape measured on one machine transfers as curvature about that point, not as
an absolute speed. The heat-pump models bind their own rated point (40 rev/s
air-to-water, 60 rev/s air-to-air, :mod:`tmhp.compressor_speed`).

What is deliberately absent
---------------------------

No low-load cliff, and no refrigerant-specific coefficient set. The residuals
were stratified by refrigerant, compressor type, source, speed and pressure
ratio; no stratum with three or more machines asked for its own coefficients.
The rotary type is represented by eight modern R-290 rated points and three
2004 R-22 maps; the modern rotaries sit at the population level (7–9 % in
their stratum), the 2004 machines 27 % below it, so the shape carries over and
the level of an old induction-motor rotary does not. A modern multi-speed
rotary map is still the first data gap (see the report archive).
See :doc:`part-load` for why inventing a low-load roll-over would contradict
the certified measurements.


Does the coefficient set survive both tests?
--------------------------------------------

Every compressor description here was fitted on compressor measurements, not
on heat pumps. Whether that was worth anything is a separate question with two
halves:

* **Shape.** EN 14825 lowers the required duty and the flow temperature together
  across four test points, and Heat Pump Keymark publishes what real machines
  declare at each of them. A coefficient set can be checked against the
  certified *population* (:doc:`part-load`).
* **Level.** A set can sit on the certified median and still miss every
  individual machine. That is what the catalogue parity set answers (:doc:`index`).

Four compressor descriptions were run through both, identically, with the
current model code:

.. figure:: ../_static/validation_en14825_verdict.svg
    :alt: Three panels: EN 14825 trajectories for the low- and
        medium-temperature applications against the Keymark band for four
        compressor descriptions, and a plane placing each description against
        catalogue parity error and distance from the certified median.
    :align: center
    :width: 100%

    **(a, b)** The certified trajectory, low- and medium-temperature
    application. **(c)** Both requirements at once.

.. list-table::
    :header-rows: 1
    :widths: 26 40 17 17

    * - Description
      - What it is
      - Parity MAPE (air-to-water)
      - Bias

    * - ``ideal``
      - all three efficiencies pinned at 1.0 — what ASHP did before this work
      - 32.1 %
      - +32.1 %

    * - ``constant``
      - the ``v2026-09-24`` correlations frozen at their rated point, no speed
        dependence
      - 7.1 %
      - +1.4 %

    * - ``legacy-v1``
      - the pre-refit defaults: ``0.90 − 0.02 PR``, the one-machine leakage term,
        the Guth speed shape at 0.80 (``validation/coefficients/v1-legacy/``)
      - 12.4 %
      - −11.5 %

    * - **``defaults``**
      - **what TMHP ships — coefficients ``BITZER-three-C3-absolute-N-2026-10-06``
        with their guards**
      - **8.4 %**
      - **−4.4 %**

Parity figures are the ten adopted air-to-water units; the held Fujitsu
catalogues are excluded from every headline. The ``defaults`` row is the
air-to-water row of :doc:`index`. Every row carries the frost and defrost
adjustment of the air-source boiler (:ref:`ashpb-frost`), which lowers each
description's COP by about a fifth at and below +2 °C outdoor; without it the
four rows read 48.8 / 16.4 / 7.4 / 10.6 % with biases of +48.8 / +14.0 / −1.1 /
+6.4 %.

Three things to read off it.

**The compressor description carries most of the model.** An ideal compressor
is 32.1 % out on the catalogues. The shipped fits come to 8.4 % with a −4.4 %
bias over the 132 points the compressor envelope admits; the speed-independent
``constant`` set comes to 7.1 % and the pre-refit ``legacy-v1`` set to 12.4 %.
The order of those three is not the one they had without the frost
adjustment (16.4, 7.4 and 10.6 %): it moves all of them down together at the
cold points, so which sits nearest the catalogues depends on that adjustment
as much as on the compressor.

**The shape passes, at every configuration.** Across fifteen combinations of
capacity, refrigerant and sizing ratio the modelled COP rises at every step
A → D in all fifteen runs of each application (96.9 % of the 9,062 certified
low-temperature records and 98.7 % of the 9,044 medium-temperature ones do the
same), and the A-to-D gradient lands inside the certified p10–p90 in all thirty
runs: median 2.35 modelled against 2.63 certified for the low-temperature
application and 3.09 against 2.86 for the medium-temperature one. Frost lowers
point A and so steepens both; without it the two medians were 2.07 — near the
p10 of 2.02 — and 2.71.

**The level passes for R32 and R410A, and misses high for R290.** Read as
percentiles of the certified population, the R32 low-temperature trajectory
sits at the 82 / 53 / 32 / 38th percentile at points A / B / C / D, and the
medium-temperature one at 20 / 70 / 66 / 52. Twenty-five of the thirty runs
have all four points inside the certified p10–p90: every R32 and R410A run of
both applications, and one R290 run. The other five R290 runs sit above the
90th percentile at one or more points (B in four of them): the default
efficiency maps make R290 the most efficient of the three refrigerants at
these conditions, more so than the certified R290 machines are.

Certified COP is measured with defrost, and what the model does about frost
decides this criterion. Without any adjustment the same R32 trajectories sat
at 94 / 80 / 32 / 38 and 70 / 91 / 66 / 52 — above the 90th percentile at
point A of the low-temperature application in every configuration, three runs
passing. With the EnergyPlus adjustment applied in full at every load they
sat at 78 / 5 / 32 / 38 and 18 / 4 / 66 / 52 — point B (+2 °C, 54 % of the
design load) below the 10th percentile in 24 of the 30 runs, one run passing:
that adjustment is a function of outdoor temperature and humidity alone and
corrects a unit at half load like one flat out. With the load on the coil
taken into account (:ref:`frost at part load <ashp-frost-load>`; no constant added) point A keeps
nearly the whole correction and point B a third of it. At the light points
the certified COP also carries on/off cycling, which the running-machine
figures here leave out; points C and D sit below the median anyway. No
result here moves a coefficient; all three are recorded (:doc:`index`).

.. admonition:: What changed on the medium-temperature trajectory
    :class: note

    An earlier edition of this page marked point C of the medium-temperature
    application as an artefact: every description dropped to 77 % of the
    certified median there because the operating-point search starved the
    outdoor coil below the compressor speed floor. That search now scores
    candidates by electrical input per unit of heat delivered
    (:mod:`tmhp._opt_utils`), and the point sits in line with its neighbours
    (66th percentile). See :doc:`part-load`.

Reproduce both halves with::

    uv run python -m validation.en14825_seasonal_trend.air_to_water --jobs 6
    uv run python -m scripts.validation.en14825_verdict_figure

or regenerate everything with ``scripts/regen-validation.sh``. The study reads
the certified population from ``validation/data/keymark_en14825_declared.csv``,
which is not committed; ``validation.extraction.keymark_declared`` builds it
from the hplib archive listed in ``validation/registry/sources.yaml``.


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


.. note::

   The compressor-efficiency derivation above (`Compressor efficiency`_)
   archives the 2026-09-24 correlations. The shipped defaults follow the
   `BITZER three-efficiency Notion study <https://app.notion.com/p/3ee6947d125d80f4830ffcb408fa647d>`_
   (2026-10-06): three independent quadratic fits in pressure ratio and
   absolute shaft speed N [rev/s], with the guards in the `Summary`_. The
   comparison in the previous section and the results on :doc:`index` are for
   those shipped defaults.
