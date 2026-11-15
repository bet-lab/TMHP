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
    * - Outdoor coil conductance (per role)
      - Leaving-end approach 2.73 K as a condenser (``UA_ou_rated`` of
        ``AirSourceHeatPump``, air at 35 °C) and 2.82 K as an evaporator
        (``UA_ou_evap_rated``, and ``UA_ou_rated`` of
        ``AirSourceHeatPumpBoiler``, air at 7 °C), at the rated outdoor airflow
      - Saturation temperature at the printed pressure of 120 / 87 operating
        points (11 / 8 product lines, 4 / 2 manufacturers) of split and
        air-to-water outdoor units; the law chosen by product-line hold-out
      - ``default_inputs.hx_approach``
    * - Indoor coil conductance (air-to-air)
      - ``UA_iu_rated = 0.16 × hp_capacity``, provisional
      - The earlier rule, no longer tied to the outdoor coil. The wall-mounted
        indoor-coil rows come from one manufacturer and disagree on the
        leaving approach, so no value is issued (`Heat-exchanger conductance`_)
      - ``default_inputs.hx_approach``
    * - Indoor coil conductance (water-to-air)
      - Leaving-end approach 2.79 K at the rated indoor airflow
        (``UA_iu_rated`` of ``GroundSourceHeatPump``, both modes)
      - 113 operating points of water-to-air units (5 product lines, 2
        manufacturers)
      - ``default_inputs.hx_approach``
    * - Liquid–refrigerant exchanger conductance
      - Leaving-end approach 4.06 K as a condenser and 3.47 K as an evaporator
        at the rated loop flow (``UA_ground_rated`` / ``UA_ground_evap_rated``
        of ``GroundSourceHeatPump``; the evaporating value is ``UA_ground`` and
        ``UA_water`` of the liquid-source boilers)
      - 117 / 118 operating points of the coaxial source exchangers of
        water-to-air units (5 product lines, 2 manufacturers)
      - ``default_inputs.hx_approach``
    * - Plate condenser of the boilers (``UA_tank_hx``)
      - Leaving-end approach 2.01 K with circulating water at
        ``m_dot_w_rated = hp_capacity / (c_w · dT_w_rated)``, ``dT_w_rated``
        5 K
      - 20 operating points of the plate condensers of air-to-water units (3
        product lines, 2 manufacturers)
      - ``default_inputs.hx_approach``
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

Every model generates its heat-exchanger conductances from a single declared
capacity, so what has to be established is a capacity-normalised rule for each
exchanger — not any particular machine's conductance. Real machines do not
publish a conductance either. What their service manuals, installation manuals
and test reports print at a rated point is the refrigerant pressure at a
service or compressor port, next to the entering air or water temperature, the
flow and the capacity. That is evidence at the level of the heat exchanger, and
it is what the cycle needs: the conductance is only the means by which the
model arrives at the saturation temperature the compressor works against.

What the conductance means
--------------------------

Every refrigerant-side exchanger in TMHP is an isothermal refrigerant against
a single-phase fluid, ``ε = 1 − exp(−UA/C)`` and ``Q = ε C |T_in − T_sat|``.
There is no refrigerant pressure drop and no desuperheating or subcooling
zone, so the model's saturation temperature has to be the one at the
compressor's ports for the pressure ratio to be right. A default conductance is
therefore defined as the **effective** conductance that reproduces, at the
rated point, the saturation temperature at the pressure the machine prints.
Line and coil pressure drop and the single-phase zones are inside it; it is not
the ``U·A`` one would compute from the coil geometry.

Which approach temperature transfers
------------------------------------

For one operating point, the inlet temperature difference
``ITD = |T_in − T_sat|``, the leaving-end approach ``ITD − Q/C``, the LMTD and
``UA/Q`` convert into each other exactly. The choice only matters when a value
measured on one machine is carried to another of a different capacity and flow:
what is held fixed? That is a question the data can answer. Per exchanger and
refrigerant role, each candidate law is fitted with one product line held out
(product lines that share hardware count as one) and scored by how well it
predicts the saturation temperature of the held-out units at their own flow and
duty. The rule, fixed before the data were collected: the simplest law within
10 % of the lowest error ships, if the stratum has at least three product lines
and two manufacturers.

.. list-table:: Product-line hold-out error of the predicted saturation temperature (RMSE)
    :header-rows: 1
    :widths: 40 20 20 20

    * - Exchanger and role
      - Fixed leaving approach
      - ``UA ∝ duty``
      - Fixed ``ITD``
    * - Outdoor coil, condensing
      - 1.63 K
      - 1.96 K
      - 3.13 K
    * - Outdoor coil, evaporating
      - 1.37 K
      - 1.49 K
      - 1.84 K
    * - Plate condenser of air-to-water units
      - 1.17 K
      - 1.53 K
      - 2.22 K
    * - Coaxial source exchanger, evaporating
      - 1.58 K
      - 1.61 K
      - 1.99 K
    * - Coaxial source exchanger, condensing
      - 2.87 K
      - 3.14 K
      - 4.73 K
    * - Water-to-air indoor coil, condensing
      - 2.72 K
      - 2.83 K
      - 3.73 K

The leaving-end approach wins or ties in every stratum a model uses, and a
fixed inlet difference — the obvious convention — is the worst everywhere. The
reason shows in the data: manufacturers build several capacities on one
chassis, and on a shared coil the inlet difference grows with the load while
the leaving approach stays put. Holding it fixed gives the conductance in
closed form,

.. math::

    UA = C \ln\!\left(1 + \frac{\dot Q}{C\,\Delta T_{\mathrm{out}}}\right)

with ``C`` the fluid's heat capacity rate at the reference flow and ``Q̇`` the
exchanger's own duty. A solution exists for every positive duty and flow. A
fixed inlet difference has none once ``Q̇ ≥ C · ITD`` — a reference flow 15 %
below the one assumed can be enough — and conductance per unit capacity
ignores the flow altogether.

The duty is the exchanger's, not the nameplate: as a condenser the outdoor coil
of a split unit rejects 1.24 W per watt of cooling nameplate (the nameplate
plus the compressor work), and as an evaporator absorbs 0.76 W per watt of
heating nameplate, both archive medians
(:func:`tmhp.default_inputs.hx_duty_per_nameplate`). The reference flow is the
rated airflow of the coil (`Maximum fan airflow`_), the rated loop flow, or the
rated circulation flow of a plate condenser.

What the archive says
---------------------

.. list-table:: The strata the models use (``validation/default_inputs/hx_approach``)
    :header-rows: 1
    :widths: 30 26 16 14 14

    * - Exchanger and role
      - Used for
      - Rows / product lines / manufacturers
      - Leaving approach, median (p10–p90)
      - Inlet difference, median
    * - Outdoor coil, condensing
      - ``AirSourceHeatPump`` ``UA_ou_rated``
      - 120 / 11 / 4
      - 2.73 K (0.9–3.9)
      - 9.4 K
    * - Outdoor coil, evaporating
      - ``AirSourceHeatPump`` ``UA_ou_evap_rated``; ``AirSourceHeatPumpBoiler``
        ``UA_ou_rated``
      - 87 / 8 / 2
      - 2.82 K (2.0–4.5)
      - 6.8 K
    * - Plate condenser of air-to-water units
      - ``UA_tank_hx`` of the three boilers
      - 20 / 3 / 2
      - 2.01 K (0.7–3.3)
      - 7.0 K
    * - Coaxial source exchanger, condensing
      - ``GroundSourceHeatPump`` ``UA_ground_rated``
      - 117 / 5 / 2
      - 4.06 K (1.6–6.5)
      - 10.2 K
    * - Coaxial source exchanger, evaporating
      - ``GroundSourceHeatPump`` ``UA_ground_evap_rated``;
        ``GroundSourceHeatPumpBoiler`` ``UA_ground``;
        ``WaterSourceHeatPumpBoiler`` ``UA_water``
      - 118 / 5 / 2
      - 3.47 K (1.0–6.6)
      - 7.7 K
    * - Water-to-air indoor coil, condensing
      - ``GroundSourceHeatPump`` ``UA_iu_rated``
      - 113 / 5 / 2
      - 2.79 K (1.5–6.6)
      - 15.9 K

The 59 sources are manufacturer service and installation manuals (Mitsubishi
Electric, ClimateMaster, WaterFurnace, Carrier, Bosch, Toshiba, Gree), the
AHRI low-GWP AREP system test reports, an ORNL mini-split test and a KTH
air-to-water prototype; the archive's README lists them with their hosts, the
conversions and the anomalies the documents contain. Outdoor coils are pooled
across product classes: split and air-to-water outdoor coils are one
construction and their leaving approaches agree (as condensers 2.5 K for
wall-mounted splits, 2.3 K for ducted splits and 3.7 K for air-to-water units).
Indoor coils are not pooled — 6.8 K for wall-mounted units, 2.6 K for ducted
ones, 2.8 K for water-to-air units — and each model takes the stratum of its
own kind of indoor unit, where one ships.

The same outdoor coil has a smaller effective conductance as an evaporator than
as a condenser (a median ratio of 0.72 over the units printed in both modes),
so each role takes the law of its own stratum: ``AirSourceHeatPump`` has
``UA_ou_rated`` for cooling and ``UA_ou_evap_rated`` for heating, and
``GroundSourceHeatPump`` ``UA_ground_rated`` and ``UA_ground_evap_rated``. A
conductance you pass for the first and not the second serves both roles; a
value you give is never split.

These values were not chosen by catalogue COP: the laws and their constants
come from the archive and the hold-out rule alone, and the catalogue comparison
of :doc:`index` was run only after they were fixed. With them the comparison
still under-predicts, so the conductance is not where the remaining bias sits.

Every default conductance is recorded in
``model.default_input_estimates["UA_…"]`` with its law, the spread of the
population, the hold-out error, the sources and whether the duty lies outside
the archive's range (``extrapolated``). A conductance you pass is used as given
and has no entry there.

What it means for a default machine
-----------------------------------

.. list-table:: Default conductance [W/K], previous rule → now (``Q`` = ``hp_capacity``)
    :header-rows: 1
    :widths: 34 18 16 16 16

    * - Conductance
      - Previous rule
      - 3.5 kW
      - 8 kW
      - 15 kW
    * - ``AirSourceHeatPump`` ``UA_ou_rated`` (condensing)
      - ``Q/5``
      - 700 → 801
      - 1600 → 1677
      - 3000 → 3025
    * - ``AirSourceHeatPump`` ``UA_ou_evap_rated`` (evaporating)
      - ``Q/5``
      - 700 → 639
      - 1600 → 1360
      - 3000 → 2470
    * - ``AirSourceHeatPump`` ``UA_iu_rated``
      - ``0.8 × Q/5``
      - 560 → 560
      - 1280 → 1280
      - 2400 → 2400
    * - ``AirSourceHeatPumpBoiler`` ``UA_ou_rated``
      - ``0.7 × Q/5``
      - 490 → 586
      - 1120 → 1214
      - 2100 → 2173
    * - ``UA_tank_hx`` of the three boilers
      - ``Q/5``, no flow
      - 700 → 875
      - 1600 → 1999
      - 3000 → 3749
    * - ``GroundSourceHeatPump`` ``UA_ground_rated`` (condensing)
      - ``0.18 × Q``
      - 630 → 682
      - 1440 → 1559
      - 2700 → 2923
    * - ``GroundSourceHeatPump`` ``UA_ground_evap_rated`` (evaporating)
      - ``0.18 × Q``
      - 630 → 575
      - 1440 → 1314
      - 2700 → 2464
    * - ``GroundSourceHeatPump`` ``UA_iu_rated``
      - ``0.08 × Q``
      - 280 → 544
      - 640 → 1082
      - 1200 → 1900
    * - ``UA_ground`` / ``UA_water`` of the liquid-source boilers
      - ``0.18 × Q``
      - 630 → 549
      - 1440 → 1255
      - 2700 → 2354

For the outdoor coil as a condenser the new evidence lands almost exactly on
the previous rule, which was derived from different data by a different route
(`The previous rule`_). The larger changes are elsewhere: an outdoor coil as an
evaporator now has about four fifths of its condensing value; the
indoor coil of the ground-source model has 1.6 to 1.9 times the conductance; and
the boilers' sink is a different structure, so its ``UA_tank_hx`` is not
comparable with the earlier one by itself — what the cycle sees is ``K_tank_hx``
(next section), which is below the earlier ``Q/5``. Small machines get more
outdoor conductance per watt than large ones because their rated airflow per
watt is larger (the airflow laws have an intercept).

.. admonition:: How wide the band is, and what that costs you
    :class: warning

    The leaving approach of a stratum is a *median*. Between the 10th and 90th
    percentile of the product lines it spans 0.9–3.9 K for outdoor coils as
    condensers, 2.0–4.5 K as evaporators, 0.7–3.3 K for plate condensers,
    1.0–6.6 K and 1.6–6.5 K for coaxial source exchangers as evaporators and
    condensers, and 1.5–6.6 K for water-to-air indoor coils. That width is
    real hardware, not measurement noise: at the same duty and flow, two
    machines at either end of the outdoor condensing band condense about 3 K
    apart. Even the best law misses the saturation temperature of a held-out
    product line by 1.2 to 2.9 K (RMSE).

    So a single vendor-neutral default describes a typical machine, and **will
    under-predict a high-efficiency product line and over-predict a
    lower-efficiency one**. If you know the machine, pass its conductances
    (``UA_ou_rated``, ``UA_ou_evap_rated``, ``UA_iu_rated``,
    ``UA_tank_hx`` …). :doc:`index` backs the conductance out of each
    validation unit's own rating point, and decomposes the rest of the
    residual, which is not all conductance.

    .. note::

        Quote the band on the same basis as the rule. The archive's spread is
        a spread of leaving approach; its spread in ``UA`` per watt also
        carries the spread of airflow per watt, and is wider.

.. admonition:: Where this does not apply
    :class: warning

    * **Wall-mounted indoor coils.** No value is shipped. Every row that gives
      duty, airflow and pressure at one point comes from one manufacturer
      (Mitsubishi Electric, six product lines), and the evidence disagrees
      on the quantity the law uses: 6.8 K of leaving approach on the declared
      duty and airflow, about 3.6 K on the discharge-air temperature the same
      tables print, about 3.3 K from a second manufacturer's pipe temperatures
      at an assumed rated load. The inlet difference agrees at 22 K. So
      ``AirSourceHeatPump`` keeps ``UA_iu_rated = 0.16 × hp_capacity``, no
      longer tied to the outdoor coil, and records it as provisional with
      that reason.
    * **Frosted operation.** Frosting rows are excluded; frost and defrost are
      handled by the defrost adjustment, not by the conductance.
    * **Microchannel coils.** The archive has none; the strata are
      fin-and-tube coils.
    * **Wet cooling coils.** Indoor coils run wet in cooling and TMHP's coils
      are dry, so those rows are excluded. The indoor values come from the dry
      condensing role and are used in both modes.
    * **Immersed or wrapped tank coils.** No measured row pairs a condensing
      pressure with a tank temperature, so no value is issued. Build the
      boiler with ``m_dot_w_rated=0`` and pass ``UA_tank_hx``.
    * **Water-to-water machines.** The archive's water-to-water rows come from
      one manufacturer whose heating tables print suction pressures below
      their own low-pressure cut-out; the liquid-source boilers borrow the
      water-to-air coaxial evaporator.

The boilers' sink
-----------------

The tank heat exchanger of ``AirSourceHeatPumpBoiler``,
``GroundSourceHeatPumpBoiler`` and ``WaterSourceHeatPumpBoiler`` is an
external plate condenser fed with tank water by a circulation pump, the
structure of monobloc air-to-water units. ``T_tank_w`` is the water entering
the condenser. The rated circulation flow is
``m_dot_w_rated = hp_capacity / (c_w · dT_w_rated)`` with ``dT_w_rated``
5 K (the EN 14511 30/35 °C rating), and the heat the exchanger carries per
kelvin of condensing temperature above the entering water is
``K_tank_hx = C_w (1 − exp(−UA_tank_hx / C_w))``. ``AirSourceHeatPumpBoiler``
takes a per-call ``m_dot_w``; the liquid-source boilers run at
``m_dot_w_rated``. ``m_dot_w_rated=0`` selects the earlier form without
circulating water, ``UA_tank_hx`` working against the tank temperature
directly; with ``UA_tank_hx = hp_capacity / 5`` it is the earlier machine.

A rating condition names the *leaving* water (``W35``, ``W55``). The catalogue
comparisons and ``operating_range()`` hand a boiler the water EN 14511 enters
with — the leaving water less the standard's rise, 5 K up to 45 °C, 8 K at
55 °C, 10 K at 65 °C — and, for ``AirSourceHeatPumpBoiler``, the flow that
carries the duty at that rise (:func:`tmhp.standard_conditions.en14511_water_rise`,
:func:`tmhp.standard_conditions.en14511_condenser_water`). This replaces the
earlier convention of a tank 2.5 K below the leaving water with no flow.

The previous rule
-----------------

Until November 2026 the outdoor coil of ``AirSourceHeatPump`` had
``UA_ou = hp_capacity / 5`` in both roles and its indoor coil ``0.8 × UA_ou``;
the boilers had ``UA_tank_hx = hp_capacity / 5`` without circulating water,
the air-source boiler's outdoor coil ``0.7 × UA_tank_hx``, and the
ground-source models ``0.18 × hp_capacity`` on the source side and
``0.08 × hp_capacity`` for the indoor coil. ``hp_capacity / 5`` came from
1,414 component coils in catalogues rated under EN 328 (352 evaporators,
median ``UA/Q`` 0.190) and ENV 327 (1,062 condensers, 0.146), converted to the
nameplate basis with ``1 + 1/EER`` and corroborated by twelve Trane packaged
units' published coil geometry through the Wang, Chi & Chang (2000) air-side
correlation. The scripts under ``validation/extraction/``
(``ua_transfer_law``, ``en328_inversion``, ``trane_geometry``,
``dt_convention``) still reproduce it.

It was replaced for two reasons. The rating standards fix the temperature
difference at which a component coil is rated, so the populations say how much
conductance a coil has at that difference, not which approach a heat pump runs
at; and residential mini-splits, the product class of the air-to-air validation
units, were outside that evidence. For outdoor coils the new evidence lands on
almost the same value. An earlier version of that work also reported a pooled
regression of ``UA/Q`` against air flow (slope −0.020, R² 0.001) as evidence
that the two are unrelated; inside one rating standard they are linked by an
identity, so the regression meant nothing and was withdrawn, leaving the
overlap of the two standards' bands and the geometric route as the evidence.


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
      - 31.9 %
      - +31.9 %

    * - ``constant``
      - the ``v2026-09-24`` correlations frozen at their rated point, no speed
        dependence
      - 7.2 %
      - +1.3 %

    * - ``legacy-v1``
      - the pre-refit defaults: ``0.90 − 0.02 PR``, the one-machine leakage term,
        the Guth speed shape at 0.80 (``validation/coefficients/v1-legacy/``)
      - 12.6 %
      - −11.7 %

    * - **``defaults``**
      - **what TMHP ships — coefficients ``BITZER-three-C3-absolute-N-2026-10-06``
        with their guards**
      - **8.2 %**
      - **−4.3 %**

Parity figures are the ten adopted air-to-water units; the held Fujitsu
catalogues are excluded from every headline. The ``defaults`` row is the
air-to-water row of :doc:`index`. Every row carries the frost and defrost
adjustment of the air-source boiler (:ref:`ashpb-frost`), which lowers each
description's COP by about a fifth at and below +2 °C outdoor; without it the
four rows read 48.6 / 16.5 / 7.3 / 10.5 % with biases of +48.6 / +13.9 / −1.3 /
+6.7 %.

Three things to read off it.

**The compressor description carries most of the model.** An ideal compressor
is 31.9 % out on the catalogues. The shipped fits come to 8.2 % with a −4.3 %
bias over the 132 points the compressor envelope admits; the speed-independent
``constant`` set comes to 7.2 % and the pre-refit ``legacy-v1`` set to 12.6 %.
The order of those three is not the one they had without the frost
adjustment (16.5, 7.3 and 10.5 %): it moves all of them down together at the
cold points, so which sits nearest the catalogues depends on that adjustment
as much as on the compressor.

**The shape passes, at every configuration.** Across fifteen combinations of
capacity, refrigerant and sizing ratio the modelled COP rises at every step
A → D in all fifteen runs of each application (96.9 % of the 9,062 certified
low-temperature records and 98.7 % of the 9,044 medium-temperature ones do the
same), and the A-to-D gradient lands inside the certified p10–p90 in all thirty
runs: median 2.45 modelled against 2.63 certified for the low-temperature
application and 3.06 against 2.86 for the medium-temperature one. Frost lowers
point A and so steepens both; without it the two medians are 2.13 — near the
p10 of 2.02 — and 2.67.

**The level sits in the upper half of the certified population, and above
its 90th percentile at point A.** Read as percentiles of the certified
population, the R32 low-temperature trajectory sits at the 92 / 76 / 59 / 64th
percentile at points A / B / C / D, and the medium-temperature one at
47 / 89 / 83 / 76. Fourteen of the thirty runs have all four points inside the
certified p10–p90: five of the fifteen low-temperature runs (R32 three of
nine, R410A two of three) and nine of the medium-temperature ones (R32 six of
nine, R410A all three). The others are above the 90th percentile at one point:
A in ten low-temperature runs, B in six medium-temperature ones. No R290 run
passes — the default efficiency maps make R290 the most efficient of the three
refrigerants at these conditions, more so than the certified R290 machines
are.

This criterion was met by twenty-five of the thirty runs until November 2026
(82 / 53 / 32 / 38 and 20 / 70 / 66 / 52 for the same R32 trajectories), and
what moved it is a default input, not a coefficient. The displacement a bare
constructor gives an R32 unit is now the one compressor catalogues give for
its capacity, 0.65 of the earlier constant (`Compressor displacement`_). The
compressor therefore turns faster for the same duty — 52 instead of 34 rev/s
at point A of the 9 kW unit — and the shipped isentropic fit is higher there
(0.74 against 0.68), so the modelled COP at A rose from 3.22 to 3.40, past the
certified 90th percentile of 3.35; at the light points the smaller compressor
also turns down further before it reaches its speed floor. The rated speed is
where the catalogues put it and inside the range the fits were made on (it
was below that range before), so this is the efficiency fits read where they
were meant to be read: against the certified population they are optimistic
in the upper part of the speed range. That belongs to the same rework of the
efficiency model as the high-lift deficit
(:ref:`high-lift-deficit`); nothing here was adjusted to hide it.

Certified COP is measured with defrost, and what the model does about frost
decides this criterion as much as the compressor does. Without any adjustment
the same R32 trajectories sit at 94 / 91 / 59 / 64 and 90 / 95 / 83 / 76 —
above the 90th percentile at point A of the low-temperature application in
every configuration, no run passing. With the EnergyPlus adjustment applied in
full at every load they sit at 91 / 9 / 59 / 64 and 44 / 14 / 83 / 76 — point
B (+2 °C, 54 % of the design load) below the 10th percentile in 15 of the 30
runs, no low-temperature run passing: that adjustment is a function of
outdoor temperature and humidity alone and corrects a unit at half load like
one flat out. With the load on the coil taken into account
(:ref:`frost at part load <ashp-frost-load>`; no constant added) point A keeps
nearly the whole correction and point B a third of it. At the light points
the certified COP also carries on/off cycling, which the running-machine
figures here leave out; points C and D sit above the median, and part of that
is this omission. No result here moves a coefficient; all three are recorded
(:doc:`index`).

.. admonition:: What changed on the medium-temperature trajectory
    :class: note

    An earlier edition of this page marked point C of the medium-temperature
    application as an artefact: every description dropped to 77 % of the
    certified median there because the operating-point search starved the
    outdoor coil below the compressor speed floor. That search now scores
    candidates by electrical input per unit of heat delivered
    (:mod:`tmhp._opt_utils`), and the point sits in line with its neighbours
    (83rd percentile). See :doc:`part-load`.

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
