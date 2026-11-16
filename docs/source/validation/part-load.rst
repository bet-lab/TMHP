===================
Part-load behaviour
===================

This page exists because the assumption it started from turned out to be wrong,
and the correction changed what the library does.

The assumption was the familiar one: a real inverter heat pump peaks somewhere
around a third of load and falls away below it, so a model whose COP climbs all
the way down must be missing something. The plan was to find the missing
penalty and put it into the compressor efficiency correlations.

.. admonition:: What the certified data actually shows
    :class: important

    Heat Pump Keymark is European third-party certification: an accredited
    laboratory measures to the standard and the declared values are published.
    Reading 18,106 certified records for 9,162 air-to-water models from 169
    manufacturers:

    .. list-table::
        :header-rows: 1
        :widths: 12 14 12 14 16 16 16

        * - Point
          - Outdoor air
          - Part load
          - COP p10
          - p25
          - **median**
          - p75
        * - A
          - −7 °C
          - 0.88
          - 2.61
          - 2.78
          - **2.99**
          - 3.18
        * - B
          - +2 °C
          - 0.54
          - 4.13
          - 4.35
          - **4.53**
          - 4.78
        * - C
          - +7 °C
          - 0.35
          - 5.37
          - 5.74
          - **6.16**
          - 6.56
        * - D
          - +12 °C
          - 0.15
          - 6.40
          - 7.19
          - **7.88**
          - 8.51

    Declared COP rises monotonically from A to D. **97.3 %** of models declare a
    higher COP at the lightest test point than at the next one up, and 96.9 %
    rise at every single step. The roll-over is not there.

    Low-temperature application, average climate. Reproduce with
    ``uv run python -m validation.extraction.keymark_declared``.

The reason is straightforward once stated: EN 14825 lowers the flow temperature
as it lowers the load — 34/30/27/24 °C from A to D for underfloor heating — so
the lift falls together with the duty. The heat exchangers also become large
relative to the duty. Both effects push COP up. (This is also why "W35" and
"W55" name an *application* and not a fixed flow temperature; reading them as
fixed would put three of the four points at the wrong boundary condition.)

So the goal changed: not to manufacture a roll-over, but to carry the low-speed
loss that is genuinely measured, at the size it is measured, and to say plainly
what is not modelled. One part of that loss is not settled by compressor data:
the low-speed shape of the rotary types, where two sources disagree, was chosen
on the shape of the certified trajectory below (:doc:`defaults`), so for the
rotary types that shape is a fitted property, not a check.


Two different curves
====================

.. figure:: ../_static/validation_part_load.svg
    :alt: Left, system COP against the heat delivered as a share of the
        maximum at the same temperatures, for three conditions, from the
        compressor's top speed (left) to its speed floor (right). Each curve
        rises from the top speed to a peak and falls again towards the floor,
        and a diamond on each marks where the 9 kW nameplate duty falls. Right,
        the EN 14825 test-point trajectory against the certified Keymark band.
    :align: center
    :width: 100%

    **(a)** Load falls, temperatures held; the horizontal axis is the heat
    delivered over the maximum the same machine delivers at those temperatures,
    and the diamonds mark the 9 kW nameplate. **(b)** Load and flow temperature
    fall together, as the certification standard specifies, over the design
    heating demand. These are not the same question and they do not have the
    same answer.

Panel (a) — fixed temperatures
------------------------------

Hold the source and sink where they are and move the load over the machine's
whole speed range, from the compressor's speed floor (15 rev/s) to its top speed
(120 rev/s). The horizontal axis is the heat the machine delivers as a share of
what it delivers *at the same temperatures at its top speed* (``Q_max [W]``).
That is a different denominator from the 9 kW nameplate, and at these conditions
the two are far apart: the nameplate is a label attached to one rating point,
not a ceiling. A diamond on each curve marks where 9 kW falls. An earlier
version of this figure stopped at the nameplate and so showed only the part of
each curve at or below the 9 kW duty. With the shipped compressor efficiencies
(the default ``rotary_twin_inverter`` type) the 9 kW air-to-water model covers
these ranges:

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    * - Air / water
      - Minimum capacity (15 rev/s)
      - Maximum capacity (120 rev/s)
      - 9 kW as a share of the maximum
    * - +7 °C / 35 °C
      - 2.12 kW
      - 16.10 kW
      - 56 %
    * - +7 °C / 45 °C
      - 2.09 kW
      - 15.68 kW
      - 57 %
    * - −7 °C / 45 °C
      - 1.43 kW
      - 9.38 kW
      - 96 %

Frost and defrost are on, as shipped, so the heat is the mean over the frost
and defrost cycle (:ref:`ashpb-frost`). The maximum is the heat of the top speed;
asking for more returns the same point. At −7 °C the maximum is 9.38 kW against
15.68 kW at +7 °C and the same water temperature, so there the nameplate is
nearly the maximum.

.. list-table::
    :header-rows: 1
    :widths: 16 10 30 18 18 8

    * - Air / water
      - Peak COP
      - Peak at
      - COP at maximum (change from peak)
      - COP at minimum (change from peak)
      - COP at 9 kW
    * - +7 °C / 35 °C
      - 4.53
      - 44 % of maximum, 79 % of nameplate, 45 rev/s
      - 3.79 (−16.5 %)
      - 3.72 (−18.0 %)
      - 4.46
    * - +7 °C / 45 °C
      - 3.51
      - 54 % of maximum, 94 % of nameplate, 56 rev/s
      - 3.20 (−8.9 %)
      - 2.56 (−27.1 %)
      - 3.51
    * - −7 °C / 45 °C
      - 2.40
      - 46 % of maximum, 48 % of nameplate, 48 rev/s
      - 2.28 (−4.9 %)
      - 1.81 (−24.5 %)
      - 2.30

Each curve rises from the speed floor to a peak and falls again towards the top
speed: higher COP at an intermediate speed than at either end, with the peak a
little under half of the maximum at 7/35 °C and −7/45 °C and a little over half
at 7/45 °C. The peak positions are grid values (the sweep steps the request by
2 % of the maximum), and on the nameplate axis they sit at different places —
79 %, 94 % and 48 % — because the nameplate is a different fraction of the
maximum at each condition. The COP at the 9 kW duty is 1.7 %, 0.1 % and 4.2 %
below the peak. Both ends fall: from the peak to the top speed COP falls by
16.5 %, 8.9 % and 4.9 %, and from the peak to the speed floor by 18.0 %, 27.1 %
and 24.5 %.

The same numbers on the nameplate axis, the way this page used to give them:
the model loses 16.7 % between the 9 kW duty and the speed floor (which delivers
2.12 kW, 24 % of the nameplate) at 7 °C air / 35 °C water, and 27.0 % at 7/45 °C.
At −7/45 °C it loses 21.2 % (COP 2.30 at the nameplate duty, 2.37 at 75 % of it,
1.81 at the floor, which delivers 1.43 kW, 16 % of the nameplate).

.. admonition:: Getting the maximum capacity of a machine
    :class: note

    A request above what the machine can deliver is answered at the top speed
    rather than refused. ``AirSourceHeatPumpBoiler.analyze_steady`` then returns
    the heat of the top speed with ``capacity_clamped == "max"`` and
    ``capacity_binding == "compressor"``, that heat as ``Q_max [W]``, and the
    request as ``Q_request [W]``; a larger request gives the same point. Below
    the ceiling ``Q_max [W]`` is NaN, so the maximum at a condition is read from
    a deliberately large request (the figure uses three times the nameplate,
    ``scripts.validation.part_load_figure.maximum_capacity``). When another
    limit binds before the compressor's top speed, ``capacity_binding`` names it
    (``discharge_temperature`` or ``condensing_temperature``, for example) and
    the heat is that limit's. The air-to-air ``AirSourceHeatPump`` has the
    same ceiling as a method: ``max_capacity(T0, T_a_room, mode=...)["Q_max [W]"]``.

The −7/45 °C curve carries the frost and defrost adjustment, which is on by
default (:ref:`ashpb-frost`) and follows the load on the outdoor coil
(:ref:`frost at part load <ashp-frost-load>`): it costs more at a higher load and
eases as the load falls, and that easing is the rise from the nameplate duty to
75 % of it. Without it (``defrost=False``) the same curve reads COP 2.75 at the
nameplate duty (91 rev/s), 2.76 at 75 % of it and 1.81 at the speed floor (16 %
of the nameplate): flat to 75 % of the nameplate and falling below it. The fall at the low-load end is the
compressor model's: at the floor the frosted point runs at 15 rev/s and a
pressure ratio of 4.7, with isentropic efficiency 0.553 against 0.793 at the
nameplate duty. That is an extrapolation of a provisional shape -- the rotary data end at
30 rev/s, and the speed shape below it is the form followed past its data
(``efficiency_extrapolated``; :doc:`defaults`). Read the low-load end of these
curves accordingly.

Two effects set the frost-free shapes: the heat exchangers grow large relative
to the duty, which raises COP, and the compressor loses efficiency at low
speed, which takes it back. Their balance is measured by
``validation/fixed_boundary_plr/decompose`` on the running cycle (no on-off
averaging below the floor): with the three efficiencies frozen at their rated
values the same model gains 22.0 % between full load and the speed floor (R32,
7 °C air, water entering at 42.5 °C) and with the shipped efficiencies loses
18.3 %; the air-to-air model, at pressure ratios of 2.4 to 2.8 at full
load, keeps +33 to +40 % with the shipped efficiencies. None of these shapes is
prescribed.

Below the floor the curves end: every request there resolves to the same
operating point (see the note below), which is the one point the curve reaches at
its low end. The fall above the floor is the
compressor efficiencies' -- an earlier page stated that the efficiency
correlations were not the cause of a turn-over, which held for the efficiency
map of then and does not hold now.

.. admonition:: Below the speed floor the model delivers its minimum capacity, and says so
    :class: note

    Once the compressor can go no slower the model cannot follow a smaller
    request. The tank-side condenser is closed on the heat the machine actually
    delivers at that speed rather than on the heat that was asked for, so every
    request below the floor resolves to the same operating point: same speed,
    same delivered capacity, same condensing temperature, same COP. (Closing it
    on the request instead made the condensing temperature follow a request the
    machine could not meet, and the reported COP rose as the request fell.)

    Earlier versions matched a sub-floor request anyway by starving the outdoor coil --
    the operating-point search minimised absolute electrical input, and among
    candidates that all sat at the speed floor the one delivering *less* heat
    also drew less power, so the outdoor fan was driven to its 5 % bound and the
    air-side temperature drop reached 17 K against a catalogue maximum near 8 K.
    That was a model artefact, and it has been removed: the search now compares
    candidates on electrical input per unit of heat delivered and rejects any
    that fall short of the request (:mod:`tmhp._opt_utils`). Below the floor the
    model therefore reports ``capacity_clamped == "min"`` together with the heat
    it actually delivers, which exceeds the request -- the *requested* part-load
    ratio and the *actual* capacity ratio are two different numbers, and the
    output carries both.

    Under the same conditions as before (air 7 °C, water entering at 42.5 °C,
    9 kW R32) the outdoor fan now stays above 52 % of rated flow over the whole
    sweep and the air-side temperature drop stays within 5.35 K; the air-to-air
    sweep stays above 48 % and 6.5 K. A real machine below its modulation floor cycles. The
    sweeps on this page were run before the air-to-water model reported that
    cycling (see below), so their sub-floor rows are a continuous-operation
    figure at the minimum capacity, not a part-load COP.
    They are drawn hollow in the figures and excluded from every trend verdict.

    At −7 °C air and 45 °C water the requests below the floor resolve to the
    minimum-capacity point as well (COP 1.81 at the floor, reached at 16 %
    load). They used to fail with ``hx_not_converged``: the outdoor-approach
    search probed only the ends of its range and missed the narrow band of
    approaches the fan can serve, which is now solved from the coil's capacity
    margins at the fan flow limits.

Panel (b) — the certification trajectory
----------------------------------------

This is the curve that has to match. The modelled trajectory rises
monotonically A → D and sits inside the certified band at A, B and D; at C it
is 0.1 % below the 10th percentile (5.36 against 5.37). Points A and B carry
the frost and defrost adjustment at the load the coil carries there
(:ref:`ashpb-frost`).

.. list-table::
    :header-rows: 1
    :widths: 10 18 16 16 18 22

    * - Point
      - Condition
      - Required load
      - Delivered
      - TMHP COP
      - Keymark median (p10–p90)
    * - A
      - −7 °C / 34 °C
      - 5.28 kW
      - 5.28 kW
      - 2.95
      - 2.99 (2.61–3.35)
    * - B
      - +2 °C / 30 °C
      - 3.24 kW
      - 3.24 kW
      - 4.18
      - 4.53 (4.13–5.01)
    * - C
      - +7 °C / 27 °C
      - 2.10 kW
      - 2.15 kW
      - 5.36
      - 6.16 (5.37–6.82)
    * - D
      - +12 °C / 24 °C
      - 0.90 kW
      - 2.45 kW
      - 8.28
      - 7.88 (6.40–8.97)

The model sits between the 25th percentile and the median at A, between the
10th and the 25th at B, just under the 10th at C and between the median and
the 75th at D. In the previous release (the BITZER quadratic map) it read
3.40 / 4.79 / 6.27 / 8.35, just above the 90th percentile at A and inside the
band at B, C and D.

.. admonition:: The shape of this trajectory is not independent validation for the rotary types
    :class: important

    The default rotary speed shape was chosen between two variants on the
    shape of exactly this kind of trajectory: the EN 14825 study's 30 cases,
    the number of cases whose COP(D)/COP(A) lies inside the certified
    population's p10–p90 (:doc:`defaults`). That the modelled D/A sits in the
    band, and that the trajectory rises from A to D, therefore says nothing
    independent about the rotary types. On the study's 30 cases the present
    defaults pass that shape check in 30 and the level check (every point
    inside its p10–p90) in 9; the level check, the catalogue parity set and the
    A−7/W55 gate were not used in the choice. The previous release passed the
    level check in 18 of 30.

Without the frost and defrost adjustment the same trajectory reads 3.39 at A,
above the 90th percentile, and 4.38 at B. With the EnergyPlus adjustment
applied in full at every load — a function of outdoor temperature and humidity
alone — it reads 2.92 at A and 3.63 at B, below the 10th percentile at B: the
unit at B delivers 3.5 kW frost-free, 0.42 of what the adjustment's
coil-temperature correlation is written for, and would be corrected as if it
ran flat out. The adjustment follows the load on the coil
(:ref:`frost at part load <ashp-frost-load>`): at A the unit is at 0.94 of that
load and keeps nearly the whole correction, at B a little over a quarter of
it. No coefficient is adjusted to move any of this; the load term has none.

.. admonition:: One result nobody put in by hand
    :class: note

    Look at the *delivered* column. At points C and D the modelled machine
    cannot modulate down to the required load and delivers more than the test
    point asks for — 2.45 kW where 0.90 kW is required. (At B the 3.24 kW is
    the mean over frosting and defrost of a machine that delivers 3.46 kW
    frost-free; with the larger displacement of before November 2026 B was on
    the speed floor as well.)

    Certified machines do the same thing. 55 % of Keymark records declare a
    *higher* heat output at point D than at point C, which is the signature of
    a machine sitting on its modulation floor. TMHP reproduces that structure
    from the speed floor alone; nothing was added to produce it.


Air-to-air — fixed temperatures
-------------------------------

.. figure:: ../_static/validation_ashp_part_load.svg
    :alt: System COP (heating, left) and EER (cooling, right) of the 3.5 kW
        air-to-air model against the heat delivered as a share of the maximum at
        the same temperatures, at three outdoor temperatures each, with a diamond
        on each curve where the 3.5 kW nameplate falls.
    :align: center
    :width: 100%

    The 3.5 kW R32 air-to-air model at fixed temperatures, heating (indoor air
    20 °C) and cooling (27 °C), from the top speed (right) to the speed floor
    (left end of each curve). Diamonds mark the 3.5 kW nameplate.

The air-to-air model (``AirSourceHeatPump``, 3.5 kW R32) is swept the same way by
``validation.analysis.ashp_trend``, with the running machine and frost and
defrost off (the sweep isolates what the correlations and heat exchangers do as
the load moves), from the top speed down to below the speed floor. The
horizontal axis is again the heat delivered over the maximum at the same
temperatures (``AirSourceHeatPump.max_capacity``), and the nameplate is the
3.5 kW cooling capacity for both duties.

.. list-table::
    :header-rows: 1
    :widths: 20 14 14 16 12 16 8

    * - Condition
      - Minimum capacity
      - Maximum capacity
      - 3.5 kW as a share of the maximum
      - COP at minimum
      - Peak COP (at share of maximum, speed)
      - COP at maximum
    * - Heating, −7 °C
      - 0.63 kW
      - 4.86 kW
      - 72 %
      - 3.85
      - 4.13 (26 %, 28 rev/s)
      - 2.89
    * - Heating, +2 °C
      - 0.81 kW
      - 5.96 kW
      - 59 %
      - 5.21
      - 5.29 (19 %, 20 rev/s)
      - 3.11
    * - Heating, +7 °C
      - 0.92 kW
      - 6.60 kW
      - 53 %
      - 6.31
      - 6.31 (15 %, 16 rev/s)
      - 3.21
    * - Cooling, 35 °C
      - 1.29 kW
      - 5.90 kW
      - 59 %
      - 6.26
      - 6.26 (22 %, 15 rev/s)
      - 2.60
    * - Cooling, 30 °C
      - 1.30 kW
      - 6.10 kW
      - 57 %
      - 8.46
      - 8.46 (21 %, 15 rev/s)
      - 2.89
    * - Cooling, 25 °C
      - 1.19 kW
      - 6.30 kW
      - 56 %
      - 9.55
      - 9.85 (27 %, 19 rev/s)
      - 3.22

Three of the six curves have no peak above the speed floor: heating at +7 °C and
cooling at 35 °C and 30 °C rise as the load falls all the way to the minimum
capacity (at +7 °C the highest COP is 0.02 % above the value at the floor, a flat
end rather than a turn-over; the two cooling curves rise at every step). At 3.5
kW, heating at +7 °C goes from COP 3.21 at the maximum to 4.74 at the nameplate
and 6.31 at the minimum, and cooling at 35 °C from 2.60 to 4.41 and 6.26. The
other three peak above the floor, shallowly: the floor is 6.7 % below the peak
for heating at −7 °C, 1.5 % below at +2 °C and 3.1 % below for cooling at 25 °C.
This is a different shape from the air-to-water curves above, all three of which
fall by 18 to 27 % between the peak and the floor.


What is not modelled
====================

.. admonition:: The air-to-water low-load COP is a continuous-operation figure
    :class: warning

    Below its modulation floor a real machine cycles on and off. Every start
    pushes the refrigerant pressures away from equilibrium and re-warms the heat
    exchangers, and the standard accounts for that loss with a degradation
    coefficient applied during seasonal integration. Every air-to-water figure
    on this page shows the running machine (``AirSourceHeatPumpBoiler`` in
    continuous operation, what ``Cd_cycling=None`` returns), so **at light
    load it is optimistic against a machine that cycles**, and the size of that
    optimism is not estimated here.

    Both models now apply the standard's own default below their floor when
    asked for a steady point, and report the mean over the cycling period
    (:mod:`tmhp.cycling`): the air-to-air model (``AirSourceHeatPump``) EN
    14825 ``C_d = 0.25`` in the form written for units delivering to air
    (:doc:`/models/ashp`), the air-to-water model ``C_c = 0.9`` in the form
    written for units delivering to water (:doc:`/models/ashpb`). Those are
    the coefficients the standard prescribes for an untested unit, taken
    unchanged; they are not fitted to anything here, and ``Cd_cycling=None``
    restores continuous operation. A certified part-load COP is declared at
    the capacity the unit runs at, before that degradation, so the comparisons
    with certified values stay with the running machine.

This is not an oversight to be patched with a fitted penalty. Cuevas & Lebrun
make the same point from the hardware side: they observe the low-speed
degradation, attribute it to oil starvation, and note that the manufacturer's
own answer is to switch to on/off operation below 35 Hz rather than keep
modulating. The loss lives in the cycling, and the cycling is what the
running-machine figures leave out.

A second caution concerns the degradation coefficient itself, for anyone
tempted to change one. The published values are not interchangeable: EN 14825
writes ``PLF = 1 − C_d(1 − CR)`` with ``C_d = 0.25``, while Dongellini writes
``f_COP = CR / (1 − C_c + C_c·CR)`` with ``C_c = 0.9``. Carrying a coefficient
from one formulation into the other means something different by it.

Likewise the horizontal axis. EN 14825 divides by the *design heating demand*,
a quantity that does not depend on temperature; Kinab and Dongellini divide by
the full-load capacity at the same temperature, which does. The two differ by
roughly a factor of 1.44 over the −7 to +12 °C range, so plotting both on one
axis produces a figure that means nothing. Panel (a) and panel (b) above use
different denominators for exactly this reason, and are labelled accordingly.
Panel (a)'s denominator is the maximum capacity at the same temperatures, which
is not the nameplate either: the nameplate is 56 %, 57 % and 96 % of it at the
three conditions of panel (a), so "full load" on a nameplate axis is not the top
of the machine's range.


Reproduce
=========

.. code-block:: bash

    uv run python -m validation.extraction.keymark_declared     # the certified band
    uv run python -m scripts.validation.part_load_figure        # both panels, and validation/data/part_load_figure/
    uv run python -m validation.analysis.ashp_trend             # air-to-air sweep, validation/data/ashp_part_load_sweep.csv
    uv run python -m scripts.validation.ashp_part_load_figure   # the air-to-air figure
    uv run python -m validation.fixed_boundary_plr.sweep        # internal-state sweep, both models
    uv run python -m validation.fixed_boundary_plr.figure

.. seealso::

    :doc:`defaults`
        The compressor efficiency models and the data behind them.
