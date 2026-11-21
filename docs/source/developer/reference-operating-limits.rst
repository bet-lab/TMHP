Reference points and operating limits
=====================================

Developer naming rules
----------------------

``rated`` means a manufacturer/design reference operating point, not a
maximum operating limit. ``ref`` denotes a fixed normalization point in flow
scaling APIs. Physical/control limits use explicit ``_min``/``_max`` names;
fixed operating commands use ``_constant`` or ``_setpoint``. Ratios state their
denominator, for example ``ground_flow_ratio`` or ``fan_flow_ratio_to_ref``.
Never change a normalization denominator when expanding an operating range.

Existing thermodynamic identifiers containing ``ref`` (refrigerant), such as
``T_ref_sat_K`` and actual ``m_dot_ref``, are thermodynamic states, not scaling
references. ``m_dot_ref_rated`` is the fixed refrigerant mass-flow reference
for ground-HX scaling; when omitted it is solved at the compressor reference
state, so no external reference run is needed. ``V_cmp_ref`` is swept displacement per revolution;
it does not define speed limits. Compressor limits remain ``rps_min/rps_max``.

Ground-loop API and migration
-----------------------------

GSHP and GSHPB accept independent field-total values in L/min:

* ``ground_flow_ref_lpm``: fixed water normalization reference.
* ``ground_flow_constant_lpm``: actual constant-flow setpoint (defaults to ref).
* ``ground_flow_min_lpm`` and ``ground_flow_max_lpm``: variable-flow limits.

For the current paper, these are 24, 24, 9.6 and 36 L/min. A 36 L/min candidate
has ratio 1.5 to the unchanged 24 L/min reference. The constant strategy always
uses 24 L/min. Constant setpoints must lie within the declared control interval; the
reference remains a normalization and interpolation anchor. Do not infer an additional hardware
cap from the reference. Prescribed variable candidates must lie inside the
explicit interval; only floating-point endpoint rounding is tolerated.

The previous ``dV_b_f_lpm`` and ratio-limit constructor inputs remain adapters,
with ``DeprecationWarning`` on explicit use. New absolute inputs take precedence.
Omitting all new inputs gives the default reference flow (GSHP: capacity-based,
``0.75 × hp_capacity / (ρc × 3 K)``, 14.3 L/min for 4 kW — 20.04 L/min until
November 2026, see :doc:`../models/gshp`; GSHPB 24 L/min; ratio interval 0.2–1.2). ``dV_b_f_m3s`` remains a reference-flow
attribute for compatibility, never the final candidate's mutable state.

``analyze_steady(..., ground_flow_lpm=24)`` forces an actual candidate; the
previous ``ground_flow_ratio`` argument remains a deprecated ratio-to-reference
adapter. Results expose ``ground_flow [m3/s]``, ``ground_flow_ratio``,
``ground_flow_at_min`` and ``ground_flow_at_max``. Historical result keys
``dV_bhe_f [m3/s]`` and ``ground_flow_ratio`` remain aliases. A failed candidate
reports zero delivered flow and NaN ratio/COP, while retaining candidate diagnostics.

Internally, the optimizer varies actual volume flow [m3/s]. Pump pressure drop
uses actual flow. Rb* interpolation uses branch mass flow [kg/s] and pins the
reference/setpoint resistances; no normalization denominator enters the Rb*
physics. Grid bounds cover the control interval and compatibility anchors.
Ground UA uses fixed water/refrigerant reference flows and permits UA above its
reference value. No ratio-to-reference clamp at one is applied.

Indoor approach search domain
-----------------------------

GSHP exposes ``indoor_approach_min_K`` / ``indoor_approach_max_K`` separately
from airflow limits. Defaults are 1–25 K (1–20 K until November 2026: the rated
duty of the default indoor coil at 400 cfm/ton needs 21.6 K). An expanded water-flow interval can
require an indoor approach above 20 K to close the HX while respecting the
compressor pressure-ratio floor. The current 26 °C cooling study explicitly
uses 1–25 K (minimum evaporating temperature 1 °C). These are numerical cycle
search limits, not fan or reference limits; they must be declared alongside
the control envelope. A custom-curve regression demonstrates a feasible PLR 0.3 / 36 L/min
point above 20 K. With the new generic fan minimum of 15%, the current
paper has no steady solution at PLR 0.3; its unchanged pressure-ratio floor
and both HX duties cannot be satisfied together. This is recorded as an
infeasible requested point, with no cycling model or constraint relaxation.

Fan API and migration
---------------------

ASHP accepts ``dV_ou_fan_a_ref/min/max`` and ``dV_iu_fan_a_ref/min/max``;
GSHP accepts the indoor trio; ASHPB accepts ``dV_fan_a_ref/min/max`` [m3/s].
Existing ``*_rated`` inputs remain supported reference aliases; an explicit
``*_ref`` takes precedence. Cross-sectional area and reference fan power are
computed at that fixed reference, independently of the solver limits.

The common HX solver accepts ``dV_fan_ref/min/max`` plus
``custom_fan_curve``. The generic ASHRAE Appendix G Method 2 correlation is
used only over 0.15--1.0 of reference airflow. Requested limits below/above
that range are intersected with it; incompatible limits raise ``ValueError``.
``calc_fan_operating_point`` exposes actual flow, power and
``fan_flow_min_limit`` / ``fan_flow_max_limit``. A nondefault user polynomial
or explicit ``curve_type="custom"`` marks a custom model whose validity and
limits are the caller's responsibility; that model may allow max > ref.
Reference and hardware maximum remain independent inputs.

Constant-speed fans
~~~~~~~~~~~~~~~~~~~

A single-speed (or single-stage) fan is ``min == max``: pass
``dV_*_fan_a_min=dV_*_fan_a_max=<flow>`` and set the reference
``dV_*_fan_a_rated`` (or ``_ref``) to the same flow, so the coil UA and the fan
power are normalised at the flow the fan actually runs at. Equal limits are
accepted by ``resolve_fan_flow_limits`` for every fan; ``min > max`` and a zero
maximum remain input errors, and the generic ASHRAE curve still requires the
flow to lie inside 0.15--1.0 of the reference. The coil cannot change its
airflow, so a duty is carried only through the approach temperature: ASHPB,
GSHP and ASHP solve that single approach (the band of approaches the fan can
serve collapses to a point) and report the duty exactly. A request the coil
cannot carry is answered as for a variable fan: ASHPB runs at the largest duty
the coil carries (``capacity_clamped == "max"``, ``capacity_binding ==
"outdoor_coil"``), GSHP and ASHP fail with ``failure_reason ==
"hx_not_converged"``.

A constant-speed fan sits at both limits, so both ``ou_fan_flow_min_limit`` and
``ou_fan_flow_max_limit`` (``iu_*`` for an indoor fan) are true; the direction in which a
coil duty misses is in ``capacity_margin_W`` of the coil solve. Fan power is the
power curve evaluated at the flow ratio, so at the reference flow it is
``curve(1)`` times the reference power: 0.998262 for the default single-zone
VAV surrogate and 0.9991 for the ASHRAE correlation. Pass
``vsd_coeffs={"c1": 1, "c2": 0, "c3": 0, "c4": 0, "c5": 0, "curve_type": "custom"}``
for a power that is exactly the rated value at every flow.

``AirSourceHeatPump`` has two coils and searches two approach temperatures.
With one constant-speed fan the feasible approaches lie on a curve, with two on
a single point, and neither a grid nor a simplex lands on a set of zero width.
The model therefore nests two band solves: the coil with the narrower fan range
is closed first (its approach is a root of that coil's capacity margin), and
the other coil's band is solved at that solution and searched for the least
electrical input per unit of heat. Both fans fixed costs about 30 cycle
evaluations (0.3 s); one fixed fan about 80 (under 1 s). A fan that can
modulate over less than 2:1 (``NARROW_FAN_FLOW_RATIO``) uses the same search to
seed the ordinary one whenever the coarse pre-scan finds no operating point.

.. note::

   With a constant-speed fan the approach-temperature search domain
   (``dT_approach_bounds``, 1--30 K by default) is the only freedom the coil
   has, so it can become the capacity limit: the largest duty is then what the
   coil carries at the upper bound, reported with ``capacity_clamped == "max"``
   and ``capacity_binding == "cycle"``. The ceiling is one least-lift solve
   (:meth:`tmhp.AirSourceHeatPump.max_capacity`); widen the bounds if the coil
   is meant to run a larger approach.

The unchanged empirical equation is

.. math::

   P^* = 0.0013 + 0.1470x + 0.9506x^2 - 0.0998x^3,\quad
   x=\dot V_{actual}/\dot V_{ref}.

Source: ANSI/ASHRAE/IES Standard 90.1-2016 Appendix G,
`Table G3.1.3.15 Method 2 <https://ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/90.1-2016/90_1_2016_be_bm_bn_bo_bp_br_bs_bu_bv_cf_cl_cm_cq_ct_cu_cv_cw_cy_20210324.pdf>`_.
Method 1's rounded table gives approximately 0.03, 0.30 and 1.00 power at
0.1, 0.5 and 1.0 airflow. Raw table parity is separate from operating bounds.
`ASHRAE 2025 Fundamentals Chapter 19 <https://handbook.ashrae.org/Handbooks/F25/IP/F25_Ch19/F25_Ch19_ip.aspx>`_
describes measured/regressed fan curves and cautions against extrapolation
below a minimum ratio, with 0.15 as an example. This motivates the generic
control assumption; it is not universal heat-pump hardware data.
`90.1-2022 Addendum u <https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/90_1_2022_u_20241231.pdf>`_
adds supporting 15% turndown evidence for multizone VAV. The informative
foreword discusses 16% power at 15% airflow; this is not a normative 16%
requirement. Amended Section 6.5.3.2.1(b) deletes the old 30% power sentence
without inserting a 16% sentence. Its minimum-airflow provision includes
the design minimum outdoor-air rate and is an upper limit on the selectable
minimum, not a universal command to clamp every fan to at least 15%.
Single-zone VAV is outside the foreword's described change. No universal
heat-pump electrical floor is inferred or added. Original Windows Chrome
PDF/HTML highlights and capture metadata are archived in
``references/fan_model/``. The generic 0.15--1.0 envelope remains a TMHP
modeling choice. Direct equation values at x=0.10/0.15/0.50/1.00 are
0.0254062/0.044401675/0.299975/0.9991, without renormalizing coefficients.

``calc_UA_from_dV_fan`` supports the new ``dV_fan_ref`` keyword and its existing
rated positional argument. UA scaling stays independent of fan power bounds
and permits ratios above one for custom curves. Fan-power dictionaries accept
``fan_ref_flow_rate`` / ``fan_ref_power`` plus historical rated/design aliases,
and optional ``fan_min_flow_rate`` / ``fan_max_flow_rate``. HX solvers report
bounded actual airflow and signed duty residuals. A clamp with unmatched heat
remains infeasible and cannot enter a finite optimization objective.

Audit and validation
--------------------

The repository audit is in ``docs/audits/reference-rated-max.md``. It inventories
reference/rated/design/ratio/max identifiers in all five released heat-pump
families, common HX/fan and ground helpers, and compressor speed handling.
WSHPB has fixed (capacity-scaled by default) surface-water/tank UAs and explicit speed limits; it contains
no fan solver or rated-flow ceiling to expand. Numerical regression tests retain
old constructor behavior, and new tests verify identical component physics at
an identical actual operating point across control strategies.

Compressor efficiency baseline and speed
----------------------------------------

All five heat-pump models resolve ``eta_cmp_isen``, ``eta_cmp_vol`` and
``eta_cmp`` set to ``None`` to the default maps of
:mod:`tmhp.compressor_efficiency` (coefficient set
``ossorio-r290-te-2026-11-21``: one R290 scroll, the same maps for every
refrigerant and compressor). These represent isentropic, volumetric and
electro-mechanical efficiency, respectively; ``eta_cmp`` is fitted on the
power of compressor and inverter together, so the compressor input is that at
the inverter, with no separate drive model. The defaults are called
``model(PR, rps, state)`` with the suction state
(:class:`tmhp.compressor_efficiency.SuctionState`, which holds the evaporating
saturation temperature ``T_evap_K``). Each input also accepts a scalar, a
``function(PR)`` or a ``function(PR, rps)``. Every evaluated value must
satisfy ``0 < eta <= 1``: a value above one is set to one, a nonpositive or
nonfinite value makes the operating point infeasible, and exceptions raised
inside a user function are preserved.

The defaults take the shaft speed ``rps`` [rev/s] as an absolute speed,
relative to a fixed 70 rev/s; nothing is read relative to ``rps_rated``. When
omitted, the reference state solves the speed delivering ``hp_capacity`` and
its ``m_dot_ref_rated`` at the family's rating condition
(:doc:`/models/reference-state`). Explicit rated speed and refrigerant flow
remain available. The reference solve does not change ``rps_min``; an
omitted ``rps_max`` is derived from the rated speed it finds (see the
operating envelope below).

The default maps carry the domain of their fitting data (``validity_domain``,
a :class:`tmhp.compressor_efficiency.MapDomain`: the speeds 30–110 rev/s and
the convex hull of the measured points in pressure ratio and evaporating
temperature). Outside it the maps are followed as they are -- nothing is
held, clamped or refused there -- and
``efficiency_extrapolated(model, PR, rps, T_evap_C)`` reports whether a point
lies outside it. The earlier default models (the semi-empirical models of
2026-11-15, the quadratic map of 2026-10-06 with its validity-domain guard,
and the correlations before it) are no longer in the library; their archives
remain under ``validation/coefficients/``, and the code that evaluates the
quadratic map and the correlations is kept as records in
``validation/coefficients/recorded_maps/``.

GSHP now solves speed using ``m_dot = V_cmp_ref * rho_suction * eta_v * rps``
at each speed candidate, and evaluates isentropic efficiency before calculating
heat duty. Refrigerant work includes isentropic losses; compressor electricity
is refrigerant work divided by electro-mechanical efficiency exactly once.
Electro-mechanical losses remain outside the refrigerant heat balance.
The capacity clamp returned by the shared speed solver reports actual delivered
duty. Coupled ground-flow operation rejects clamped points that cannot meet the
requested duty. Its failed points retain unavailable COP/power semantics.

GSHP's former fixed default displacement of 100 cm3/rev over-sized its 4 kW
default capacity once speed bounds were enforced. An omitted displacement now
uses the same capacity-based default as the other models; explicitly supplied
displacement is unchanged. Deprecated GSHP ``eta_v`` / ``eta_em`` scalar inputs
map to the new interface, with explicit new inputs taking precedence.

The audit of the efficiency baseline of 2026-10 (source module SHA, original
checkout commit and coefficient snapshot) is recorded under
``docs/audits/compressor-efficiency-*``. The efficiency models describe
compressor modulation, not cycling losses or on-off duty averaging.

At the pressure-ratio floor, GSHP recomputes permitted subcooling from the
projected condenser temperature and its physical sink approach. Rejected trial
approaches therefore cannot change the refrigerant liquid state at identical
projected conditions. Coupled optimization explicitly includes the ground-HX
duty-equality boundary at the PR floor; the shipped efficiency coefficients and
existing HX, load and optimization verification tolerances remain unchanged.

Compressor efficiency range (2026-11-12)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The temporary 2026-10-07 policy of passing fitted efficiencies above one
through unchanged has ended (issue #96). Values above one are set to one, the
bound each efficiency approaches; nonpositive and nonfinite efficiencies remain
invalid.

Compressor operating envelope (2026-11-13)
------------------------------------------

The five families share one envelope, defined in
:mod:`tmhp.compressor_envelope`. A result reported as running
(``hp_is_on``, ``converged``, ``failure_reason == "none"``) satisfies all
of it.

.. list-table::
    :header-rows: 1
    :widths: 22 30 48

    * - Limit
      - Default
      - Rule
    * - ``T_dis_max`` (constructor, keyword)
      - ``"auto"``: R32 150 °C, every other refrigerant 135 °C.
        ``None`` switches the check off; a number sets it.
      - Compressor protection settings: Danfoss SH (R410A) discharge gas
        ≤ 135 °C; Danfoss PSH (R410A/R454B) wet injection from 121 °C,
        safety maximum 135 °C; Danfoss DSF (R32) external discharge-gas
        protection opening at ≤ 150 °C. Unit controllers act earlier on the
        discharge *pipe* (Daikin room units: frequency drop from about
        100 °C, stop at 110–120 °C); the model uses the protection value
        because it has no shell heat loss and puts all compressor loss
        into the gas. A point above the limit is not an operating point.
    * - ``PR_cycle_max``
      - 11 for every family (was 20 for the boilers, 5 for GSHP).
      - Single-stage scroll hardware limit. Higher ratios are two-stage or
        injection machines. With the present defaults it binds at high lift
        inside the discharge limit (the 15 kW R32 boiler at −20 °C against a
        60 °C tank is held at 4.3 kW by it). When it was set (2026-11-13, with
        the earlier efficiency map) only R134a / R1234yf boiler points at
        PR 11–15.4 reached it inside the discharge limit.
    * - ``rps_max``
      - ``None``: ``max(120, 1.5 × rps_rated)`` (until November 2026
        ``max(min(2 × rps_rated, 120), 1.5 × rps_rated)``).
      - The rated speed is solved first over ``[rps_min, 150]``. 120 rev/s is
        the top of variable-speed scroll and rotary ranges and a property of
        the compressor, not of the nameplate
        (:func:`tmhp.compressor_envelope.default_rps_max`); the adopted
        catalogues run at up to 2.59 × their rated speed (WH-UQ09HE8 at
        −15 °C / 55 °C, 118 rev/s). A machine rated above 80 rev/s keeps
        1.5 × its rated speed, the catalogue maximum-to-rated capacity ratio:
        the default ground-source boiler, rated at B0/W55, is rated at
        84 rev/s with R290 and 97 with R134a. The former fixed 150 rev/s let
        the compressor alone deliver 2.5–5.9 × nameplate. A user value is
        kept.
    * - ``rps_min``
      - 15 rev/s
      - Unchanged.
    * - ``T_cond_max`` (constructor, keyword)
      - ``"auto"``: by refrigerant (table below), never above the dew point
        at 0.9 of the critical pressure. ``None`` switches the check off; a
        number sets it.
      - The top of the compressor envelope: the highest condensing pressure,
        stated as its saturation temperature. A point above it is not an
        operating point; see *Condensing limit* below.

Condensing limit (``T_cond_max``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every published compressor envelope ends at a maximum condensing temperature,
which is a maximum high-side pressure: the shell, the scroll set and the motor
are built for it and the unit's high-pressure switch stops the compressor just
above it. Before this limit the boilers returned normal operation at pressures
no compressor is built for -- R410A charging a tank to 65 °C at 70 °C
condensing (4.76 MPa, 97 % of the critical pressure), R32 at 77.9 °C, a
top-coil stratified tank with R134a at 96 °C.

.. list-table::
    :header-rows: 1
    :widths: 14 10 76

    * - Refrigerant
      - Default
      - Basis (upper end of the high-side working range of compressors sold for the fluid)
    * - R410A
      - 65 °C
      - Copeland ZP*K3E/K5E application envelope tops at about 65.5 °C
        (replacement guidelines C050202, fig. 2); Danfoss PSH019–039 "65 °C
        saturated condensing temperature"; Danfoss DSH working range to
        41.7 bar(g) = 64.9 °C (extended models 68 °C).
    * - R32
      - 65 °C
      - Danfoss DSF090 working range high side to 43 bar(g) = 65.2 °C;
        Copeland YP (R32) is stated to cover the field of application of
        the R410A scrolls.
    * - R407C
      - 65 °C
      - Danfoss HHP heat-pump scrolls: working range high side to
        27.4 bar(g) = 65.0 °C dew point (Copeland ZR envelope: about 67 °C).
    * - R290
      - 75 °C
      - Copeland ZH04KCU–ZH16KCU heat-pump scrolls: high-side PS 28 bar(g)
        = 76.0 °C (Danfoss VZN: working range to 31.5 bar(g) = 82 °C).
    * - R134a
      - 80 °C
      - Danfoss VZN with R513A, the R134a pressure class: working range high
        side to 25.9 bar(g), 81.0 °C as R134a.
    * - R1234yf
      - 75 °C
      - No envelope published for this fluid was found; the R134a pressure
        class would allow 83.3 °C. 75 °C is about 20 K below the critical
        point, as for R290.
    * - R1234ze(E)
      - 90 °C
      - Danfoss PSG605–800: working range to 25.3 bar(g) = 93.0 °C.
    * - R600a
      - 100 °C
      - Danfoss PSG605–800: working range to 19.6 bar(g) = 102.0 °C.
    * - R717
      - 70 °C
      - The 40 bar class of ammonia heat-pump compressors (GEA Grasso V HP,
        39 bar, hot water about 70 °C); 50 and 63 bar machines reach
        82–95 °C.
    * - any other
      - dew point at 0.9 × ``P_crit``
      - No published envelope reaches it, and next to the critical point the
        condenser has almost no latent heat left. R744 gets 26.4 °C: its
        heat pumps are transcritical, which the subcritical cycle does not
        describe.

The check compares pressures: a running state is outside when its condensing
pressure exceeds the dew pressure at ``T_cond_max`` (by more than the pressure
of ``CONDENSING_TOL_K`` = 0.01 K). For a zeotropic blend the envelope
temperature is the dew point while the cycle's own condensing temperature is
the bubble point, lower by the glide: R407C reaches the 65 °C limit when
``T_ref_cond_sat_l`` is 61.2 °C. It is judged before the discharge
temperature, since it depends neither on the efficiency model nor on the
discharge-temperature control (the suction state the control moves does not
change the condensing pressure).

The condensing temperature is the demand-side temperature plus the condenser
approach, and the approach shrinks with the duty, so the limit is answered
like the other limits a smaller duty cures:

* **ASHP, GSHP** — a candidate above the limit is rejected inside the
  operating-point search. When that leaves a capacity ceiling the family
  cannot name (``"cycle"``), the same request is solved once more without the
  limit; a ceiling that then rises is reported with
  ``capacity_binding == "condensing_temperature"``. With no deliverable duty
  the result is the off state with
  ``failure_reason == "condensing_temp_above_max"``.
* **ASHPB, GSHPB, WSHPB** — the solved operating point is checked;
  ``analyze_steady`` and each dynamic step then run at the duty that condenses
  on the limit, with ``capacity_clamped == "max"`` and
  ``capacity_binding == "condensing_temperature"``. That duty is not searched
  with the ladder and bisection of the other limits: the refused request and
  the lowest duty of the ladder give two points of the condensing temperature
  over the duty, the secant gives the duty on the limit, and one solve
  confirms it (exactly, for a mixed tank whose approach is ``Q / UA``; a
  stratified tank's coil-side water warms with the duty, and the secant is
  repeated). Three cycle solves for a step that runs against the limit, two
  for one that cannot run. When the tank itself is at the limit no duty runs:
  the off state with ``failure_reason == "condensing_temp_above_max"``.
* A machine whose **rating point** condenses above the limit is refused at
  construction (``ReferenceStateError`` naming the standard, the condensing
  temperature and ``T_cond_max``): the nameplate cannot be defined outside the
  envelope. An 8 kW R410A boiler with a 500 W/K tank coil (16 K approach,
  71 °C at W55) is such a machine.

**The thermostat band is capped at the envelope's cut-out.** A boiler whose
upper set point lies above what its compressor reaches does not charge the
tank to it; a real controller stops at the unit's maximum. The thermostat has
to know that temperature: a dynamic step solves one cycle, at the tank
temperature its run averages to
(:func:`tmhp.dynamic_context.resolve_step_operation`), and applies its heat
over the step, so a cut-out the thermostat does not see is overrun inside a
long step by an amount that depends on the step (8 kW R410A, 100 L, 65 °C set
point, called at 54.9 °C: the tank stopped at 62.1 °C after 0.84 kWh with
60 s steps and at 65.0 °C after 1.19 kWh with 600 s steps, and with short
steps every later step was a called-but-refused one).
So at construction (:func:`tmhp.compressor_envelope.limit_tank_setpoints_to_envelope`):

* the cut-out is the tank temperature at which the nameplate duty condenses
  on the limit, ``T_cond_max`` (as the cycle's bubble point) less
  ``hp_capacity / K_tank_hx`` (``K_tank_hx``: the heat the tank heat
  exchanger carries per kelvin of condensing temperature above the tank
  water) -- 59.9 °C for R410A, 60.0 °C for R32, 69.9 °C for R290 with the
  tank heat exchanger of before November 2026 (a 5 K approach, no circulating
  water);
* a ``T_tank_w_upper_bound`` above it is replaced by it, and a
  ``T_tank_w_lower_bound`` that is not below it is moved down to keep the
  width of the band (the ASHPB's 60–65 °C becomes 54.9–59.9 °C with R410A);
* a ``UserWarning`` says what was changed, and
  ``T_tank_w_setpoints_requested`` keeps the values asked for.

Up to the cut-out the machine runs at its nameplate duty and the thermostat
ends the run inside the step, as at any set point: the same machine, one hour
from 50 °C, takes 1.165 kWh and stops at 59.9 °C with 60 s and with 600 s
steps alike. A set point
inside the envelope is untouched and nothing warns -- 65 °C with R290 or
R134a, which is why ASHPB, GSHPB and WSHPB default to R290. The duty reduction above
then serves ``analyze_steady`` and the steps that go past the cut-out all the
same: a stratified tank whose coil-side water is hotter than the sensor, a
minimum run time, a set point moved after construction.

.. _reduced-duty-charging:

**Charging past the cut-out at reduced duty**
(``condensing_limit_control="reduce_duty"`` on ASHPB, GSHPB and WSHPB; the
default ``"stop"`` is everything above). The cut-out is where the *nameplate*
duty meets the limit. A fixed-speed unit stops there. An inverter unit lowers
its frequency and keeps charging at the largest duty the limit leaves, which
is what ``analyze_steady`` returns against a hotter tank; ending the charge at
the nameplate cut-out understates the temperature such a unit reaches on the
heat pump alone (R32, 15 kW: 58.0 against about 63 °C). With ``"reduce_duty"``
a dynamic run follows the unit along the limit.

*The limit as a duty.* The cycle condenses at the tank temperature plus
``Q / K_tank_hx``, so on the limit the compressor delivers
``Q_lim(T) = K_tank_hx (T_reach − T)``, with ``T_reach`` the bubble point of
the limit pressure (:func:`tmhp.compressor_envelope.condensing_tank_reach`).
The nameplate cut-out is where ``Q_lim`` equals ``hp_capacity``. Above it the
tank follows ``C_tank dT/dt = K_tank_hx (T_reach − T) + P(T)`` (``P``: draw,
loss, subsystems): a first-order approach to ``T_reach`` with the time
constant ``C_tank / K_tank_hx``, 295 s for the default 151 L tank of a 15 kW
unit. An explicit step that applies one cycle's heat over an hour cannot
follow that; this is why the default caps the thermostat at the cut-out.

*Inside the step.* The in-step thermostat
(:func:`tmhp.dynamic_context.simulate_thermostat_step`, ``duty_limit``)
already integrates a linear tank equation exactly between its events. The
stretch on the limit is one more linear equation, so it is integrated exactly
too, with the knee (the cut-out) as an event. Within that event model -- the
free heat ``P(T)`` a straight line in the tank temperature, the condensing
limit the one that governs the stretch (checked per source state at three
tank temperatures; *Where it applies* below has the check and the case it
does not see) -- the run time and the heat of a step are exact
whatever the step: on the charges below, 60 s, 600 s and 3600 s steps give the
same run time to the second and the same heat to 0.05 %.

That is a statement about the events, not about every tank. The tank
temperature a step ends at does not come from them: as on every step of either
option it is one implicit solve of the tank balance with the step's mean heat
and the loss at the end-of-step temperature. The two agree as far as the loss
changes little over a step, which is the kernel's property and the same with
``"stop"``. Against a fine-step integral that uses nothing of the event model
(R32 15 kW, 15 °C outdoor, 20 minutes from 50 °C, steps of 60, 600 and
1200 s; ``tests/test_reduced_duty_charge.py``):

.. list-table::
   :header-rows: 1
   :widths: 34 33 33

   * - tank loss coefficient (``C_tank / UA``)
     - ``"stop"``
     - ``"reduce_duty"``
   * - 1.15 W/K, the default (153 h)
     - 0.003 K, 0.01 % of heat
     - 0.006 K, 0.01 %
   * - 4.6 W/K (38 h)
     - 0.010 K, 0.01 %
     - 0.022 K, 0.02 %
   * - 76.65 W/K (2.3 h)
     - 0.12 K, 0.3 %
     - 0.22 K, 1.8 %

On the limit the duty itself follows the tank temperature, so a difference in
the temperature a step ends at carries into the next step's heat; a tank that
loses a large part of the compressor's heat (the last row: 3 of 15 kW) needs
short steps with either option.

*Where the charge ends.* The compressor has a smallest duty it runs at
continuously: its speed floor, or the envelope's lower side where the
discharge runs too hot at low speed
(:func:`tmhp.compressor_envelope.smallest_continuous_duty`; the probe is the
lowest rung of the duty reduction's ladder, 10 % of the nameplate, so the
floor is the one ``analyze_steady`` has). The charging limit is the tank
temperature at which that duty condenses on the limit,
``T_reach − Q_min / K_tank_hx``
(:func:`tmhp.compressor_envelope.smallest_duty_on_condensing_limit`: usually
three cycle solves). The search stays where a tank can be: for a refrigerant
whose limit lies high enough (R600a with ``T_cond_max`` above about 101 °C)
the smallest duty would meet the limit above 100 °C, and the charge then ends
at the boiling point, as it does without a condensing limit. Above the
charging limit nothing runs. ``Q_min`` depends on the source --
denser suction gas from a warmer source gives more duty at the lowest speed --
so a step uses the limit of its own source, and the thermostat's cap is that
limit at one stated reference: the rating point's outdoor air for ASHPB, the
source at ``Ts`` for GSHPB and WSHPB (``charge_limit_reference_C``;
:func:`tmhp.compressor_envelope.limit_tank_setpoints_to_charge_limit`). Where
a step's own stop lies below the set point the compressor stops there while
the call stands, and the row says ``hp_stop_reason == "condensing_limit"``
(``"boiling_limit"`` when the stop that acted was the boiling point: the row
names the stop the step was resolved with, not the nameplate cut-out). What
follows depends on the minimum rest time. Without one (``min_off_time_s = 0``,
the default) the compressor holds the tank at the stop, cycling at ``Q_min``
against the tank's losses. That hold is the mean of on/off cycling with no
differential and no rest, reported as one run: ``hp_starts [-]`` counts the
start of the hold only, ``runtime_fraction [-]`` is the share of the time the
compressor is on, and the carried state says "running" with a phase age that
counts from that start -- the timers describe the hold, not a compressor that
has run all that time. With a minimum rest time the cycling is not something
the compressor can do, and the step resolves it as it happens: stop at the
stop, rest for ``min_off_time_s`` while the tank cools, restart (a counted
start), run back along the limit. Run time, starts and timers then describe
the same compressor (R32, 35 °C outdoor, both minimum times 1800 s, three
1200 s steps from 61.0 °C with a small draw: 27 s to the stop at 61.3 °C,
1800 s of rest, a second run of 201 s; the hold of the same case runs 521 s
as one start). A compressor on the limit near the stop runs at its lowest
speed, and the cycle there can have a little more than the duty it is asked
for (6 parts in a million for that unit at the stop). The row then takes
that cycle's rates -- heat, power, flows -- at the duty asked, with the
cycle's COP and states, so that the tank gets what the events integrated and
no more: a hold stays at the stop, and the run times are those of the
events. The excess is not small by construction; what bounds it is the 0.1 %
within which a cycle counts as delivering the duty asked
(:data:`tmhp.dynamic_context.DUTY_MATCH_RTOL`), so the rates are lowered by
at most 0.0999 %, and a cycle further off withholds the step (*Where it
applies*). Measured near that bound: the same unit with a speed floor of
8 rev/s (``rps_min=8``) at 45 °C outdoor, holding at its stop of 62.48 °C, is
asked for 5,382.86 W and its cycle delivers 5,388.21 W, so the rates are
taken times 0.99901 (0.0992 %). Heat, power and refrigerant flow are scaled
together: heat over power is still the COP of the row, and the heat is the
flow times the enthalpy difference of the row's states. The multi-node tanks of GSHPB, which keep ``"stop"``, hold the water
around their coil as a mean whatever the rest time, as before, and so does a
step with reduced-duty charging withheld at its nameplate cut-out (it is the
step of ``"stop"``). If the tank
loses more at the stop than
``Q_min`` (a draw), the compressor is not held to that duty: it runs on, the
tank cools, the limit leaves it more, and the tank settles below the stop
where the limit's duty is the loss (R32, 35 °C outdoor, a 10.5 kW draw: the
stop is 61.3 °C with 7.9 kW, the tank settles at 60.1 °C). Above the stop
the compressor does not run at all, so on the limit it never delivers less
than ``Q_min``; a tank found above the stop of its step (the source warmed, or
a collector heated it, or the run was started there) waits until it has
cooled to it, and is held from then on. The heat of the hold in that step is
the one the step's closing balance needs to end at the stop: the loss at the
stop for the whole step, less the heat the tank gave up cooling to it. (The
events have the tank lose more while it is warmer than the stop; the implicit
balance that sets the end-of-step temperature takes the loss of the whole
step at that temperature. With the heat of the events' hold the 15 kW R32
unit at 35 °C outdoor, cooling from 61.8 °C to its stop of 61.3034 °C in a
600 s step, ended the step 1.3e-4 K above the stop, and the next step counted
a stop and a second start; it now ends at the stop and the run has one
start, with 60 s, 600 s and 3600 s steps.) Where the tank reaches the stop in
the last moments of a step, so that by the closing balance it is not there
yet, the compressor starts in the next step. A step with reduced-duty
charging withheld holds at its nameplate cut-out with the kernel of
``"stop"``, unchanged, which in the same situation still counts that second
start.

*Where it applies.* The events take the condensing limit's duty for the whole
stretch from the nameplate cut-out (the knee) to the stop. That is the tank's
path only where the condensing limit is the limit that governs that stretch.
With a cold source another one can hold the compressor below it -- the source
fluid's freezing limit, the pressure-ratio ceiling, the outdoor coil at its
largest airflow, a discharge-temperature ceiling, the compressor's own speed
ceiling -- and a step integrated along the condensing limit would then
credit the tank with heat the cycle does not deliver. So the stretch is
qualified before it is used, once per source state
(:func:`tmhp.compressor_envelope.condensing_stretch`): the step's cycle is
solved at the knee, at the middle of the stretch and at the stop, each time
asked for the duty the condensing limit leaves there. The stretch qualifies if
all three run, deliver that duty to 0.1 %
(:data:`tmhp.dynamic_context.DUTY_MATCH_RTOL`; the measured ratio is
1.000000 where the condensing limit governs) and name no limit but the
condensing one. The source state is the one the smallest duty is found for,
and the verdict is kept with it: the outdoor air for ASHPB, the source water
for WSHPB, the ground as the step finds it for GSHPB (every step that gets to
the limit). That verdict depends on the machine and its source state alone --
not on the step, and not on where in the stretch the tank is. Whether a
given step of a qualified source state is withheld also depends on the step
itself, through the check of its own cycles described below.

Where the stretch does not qualify, reduced-duty charging is **withheld** for
that step. The step is then solved exactly as ``"stop"`` solves it: the same
call of the kernel with the nameplate cut-out, the same cycle solves, the same
heat, electricity and labels. The thermostat band is not changed (it stays the
one the model was built with), so a call for heat above the nameplate cut-out
stands and is not met while the source state lasts. Two keys say what
happened: ``reduced_duty_fraction [-]`` is 0, and ``reduced_duty_withheld``
names what withheld the charging on every row in which the nameplate cut-out
then acted (the compressor stopped at it, or stayed off above it;
``hp_stop_reason == "condensing_limit"``). The name is the
``capacity_binding`` of the first of the three cycles that was held below the
limit's duty (``"source_fluid_freezing"``, ``"pressure_ratio"``,
``"outdoor_coil"``, ``"discharge_temperature"``; ``"compressor"`` for the
speed ceiling), or the ``failure_reason`` of the
first that did not run (``"source_fluid_freezing"``,
``"discharge_temp_above_max"``); ``"no_smaller_duty"`` where no continuous
duty below the nameplate runs at all. On every other row the key is ``None``.
That includes every row whose stop was the boiling point
(``hp_stop_reason == "boiling_limit"``): a machine whose nameplate duty meets
the condensing limit at or above 100 °C (the water-source unit with R600a and
``T_cond_max = 108 °C``: nameplate cut-out 101.0 °C) never gets to that limit,
its steps are those of ``"stop"``, and nothing is asked about a stretch or
withheld.

Measured (R32, 15 kW, one hour from 57.5 °C;
``tests/test_reduced_duty_charge.py`` compares each of these source states
with a ``"stop"`` model given the same band, every column, no tolerance):

- outdoor −15 °C, set points 60–62 °C. At the knee (56.8 °C) the pressure
  ratio is at its ceiling of 11 and the cycle delivers 13.4 of the 15 kW the
  condensing limit would leave, so ``reduced_duty_withheld ==
  "pressure_ratio"``. The compressor runs to the nameplate cut-out and holds
  the tank there, 37 s in the hour: 57.99 °C, 0.1296 kWh of heat, 0.0816 kWh
  of electricity, the same to the last digit shown with 60 s, 600 s and
  3600 s steps, and the same rows as ``"stop"``;
- outdoor −7 °C with ``T_dis_max = 115 °C``, set points 61–63.3 °C: the
  discharge limit holds the cycle at the knee at 10.5 kW
  (``"discharge_temperature"``); 57.99 °C, 0.1296 kWh and 0.0736 kWh at the
  three step sizes;
- outdoor −7 °C with a compressor ceiling of 80 rev/s (``rps_max=80``), set
  points 60–62 °C: at its ceiling the compressor delivers 12.6 kW at the knee
  (``"compressor"``); 57.99 °C, 0.1296 kWh and 0.0750 kWh;
- the water-source boiler with source water at 4 °C and ``T_dis_max = None``,
  set points 75–76 °C (kept as 71.8–72.8 °C, the charging limit at the
  reference source), ten minutes from 68.1 °C: the source water's freezing
  limit leaves 3.1 of 8 kW at the knee (``"source_fluid_freezing"``), and the
  compressor stays off above the cut-out of 68.0 °C in 60 s steps and in one
  step alike. With source water at 3.1 °C no cycle runs at the knee, and the
  key carries the cycle's ``failure_reason``, also
  ``"source_fluid_freezing"``.

The check samples three points. A limit that acts only strictly between two
of them is not seen; the rule assumes that a limit which leaves the knee, the
middle and the stop alone leaves the stretch between them alone. A check of
each step stands behind that assumption, so that no row is credited with heat
its cycles did not deliver and no step under another limit is recorded as
reduced-duty charging: the cycles a step on the limit solves anyway -- one at
the mean of its full-duty stretch, one at the mean of its stretch on the
limit -- are read as the three qualification cycles are. Each has to deliver
the duty the events took there (the nameplate duty, the limit's duty) to the
same 0.1 %, and to name no limit but the condensing one in
``capacity_binding``. A step in which one does not, because it has no
operating point or another limit holds it, is withheld the same way, with
``reduced_duty_withheld == "cycle"`` where the stretch itself had qualified.
That goes for a full-duty cycle that fails outright too: whether the
compressor runs in that step, and with which cycle, is then what ``"stop"``
resolves (its own solves at its own mean and run time, and at the start of
the run where the cycle has no operating point at that mean).
None of the charges of the default machines measured here needed it
(delivered over asked: 1.000000).

That second check is made at the means of the step's own stretches, and
those move with the step length and with the tank temperature the step
starts from. So where the assumption fails -- a limit that acts only between
the three sampled tank temperatures -- the charge **depends on the step
size**: a step is withheld if its cycle lands where that limit acts, and
charges on the condensing limit if it lands elsewhere. Constructed for the
final review of this option (default R290 water-source unit with
``T_dis_max = 92.408 °C`` and an isentropic efficiency that dips around
35 rev/s, ``eta_cmp_isen=lambda pr, rps: 0.8 - 0.4 * exp(-((rps - 35) / 3)
** 2)``; set points 72–73 °C, kept as 72.00–72.85 °C; source water 16 °C, no
draw, one hour from 68.95 °C): at the knee (67.99 °C, 49.5 rev/s), the middle
(70.42 °C, 30.9 rev/s) and the stop (72.86 °C, 15.0 rev/s) the cycle delivers
the condensing limit's 8,000, 5,224 and 2,447 W, and the stretch qualifies.
Between the knee and the middle, where the compressor would run at about
33 rev/s, the cycle is held below that duty (seen at the points the steps
solved, 69.24 to 69.41 °C; ``capacity_binding == "source_heat_exchanger"``).
With 60 s steps the first step's stretch on the limit has its mean at
69.41 °C, is asked for 6,376 W and delivers 6,217 W, and the step is withheld
(``reduced_duty_withheld == "cycle"``, the row of ``"stop"`` with the same
band: the compressor stays off above the nameplate cut-out). The tank cools
a little in each such step, and after 34 of them the step's cycle is clear of
the limit and the charge runs. Steps of 600 s and 3600 s have their means
elsewhere and charge at once:

.. list-table::
   :header-rows: 1
   :widths: 16 20 20 22 22

   * - steps
     - tank after the hour
     - heat
     - electricity
     - rows withheld
   * - 60 s
     - 72.73 °C
     - 0.4835 kWh
     - 0.2039 kWh
     - 34 of 60
   * - 600 s
     - 72.52 °C
     - 0.4605 kWh
     - 0.1873 kWh
     - 0 of 6
   * - 3600 s
     - 72.52 °C
     - 0.4605 kWh
     - 0.1873 kWh
     - 0 of 1

That is 0.21 K, 4.8 % of the heat and 8.1 % of the electricity between 60 s
steps and the longer ones, against 0.02 K, 0.05 % and 0.4 % for the default
water-source unit, on whose charge the condensing limit governs throughout.
Every row is still consistent in itself -- none is credited with heat its cycle did not deliver, and each
withheld row is the row of ``"stop"`` -- but the run as a whole is not the
same run at two step sizes, and none of them follows the unit along the limit
that actually holds it there. The rows do not identify the situation. On the
60 s rows ``reduced_duty_withheld`` is ``"cycle"``, and that value says only
that a cycle did not run as required and gave no name of its own. It is
written for a step of a qualified stretch whose own cycle -- on the limit, or
at the full duty below the knee -- has no operating point, names another
limit, or misses the duty the events took by more than 0.1 % either way:
less, as here, or more, as a compressor at its speed floor does beyond the
bound given under *Where the charge ends*. It is also written where the
stretch itself did not qualify because one of its three cycles did not run
without a ``failure_reason``, or carries ``"cycle"`` as its
``capacity_binding``. So the value proves neither that the stretch qualified
nor that a limit lies between the sampled points; here both were established
by solving the cycles (the three of the check, and the step's own with what
it was asked, what it delivered and its ``capacity_binding``). And the 600 s
and 3600 s rows say nothing at all, because their cycles did not land on the
limit: a run without that value is not shown to be free of one
(``tests/test_reduced_duty_charge.py`` pins this case). More sampling points
would move the gap, not close it; following another limit inside the step is
the later work named under *What it does not cover*.

Which source states those are depends on the machine. For the default
machines on the default compressor map, per whole degree of the source (one
hour from 50 °C, set points above the cut-out):

.. list-table::
   :header-rows: 1
   :widths: 30 26 44

   * - machine
     - qualifies
     - withheld
   * - air-source, R290, 15 kW
     - −12 °C outdoor and above
     - −13 °C (``"outdoor_coil"``: 14.2 of 15 kW at the knee), −14 °C and
       below (``"pressure_ratio"``: 12.6 kW at −14 °C)
   * - air-source, R32, 15 kW
     - −13 °C outdoor and above
     - −14 °C (``"outdoor_coil"``: 14.8 kW), −15 °C (``"pressure_ratio"``:
       13.4 kW)
   * - water-source, R290, 8 kW
     - source water from 6 °C (to 30 °C, the warmest tried)
     - 5 °C and below (``"source_fluid_freezing"``: 6.0 of 8 kW at 5 °C,
       3.1 kW at 4 °C, no cycle at 3 °C)
   * - ground-source, R290, 8 kW
     - undisturbed ground from 3 to 20 °C (the range tried)
     - none in that range

Where it qualifies the hour ends 4.0 K warmer than with ``"stop"`` and
delivers 0.71 kWh more (15 kW units) or 0.47 kWh more (8 kW units); where it
is withheld it differs from ``"stop"`` only by the band it keeps (the call
stands and the compressor holds the tank at the cut-out: 0.01 to 0.04 kWh
more).

The qualification is strict about the knee. A compressor that falls short of
the nameplate duty there by more than 0.1 % at its speed ceiling withholds the
charging (``"compressor"``), although only the lower end of the stretch is
affected. With the default ceiling of 120 rev/s that does not occur for the
machines above: the knee cycle runs at 115 rev/s at most (R290 at −12 °C),
and in colder air the outdoor coil and the pressure ratio hold it first. It
does occur for a compressor given a lower ceiling (``rps_max``): the 15 kW
R290 unit with 100 rev/s is withheld at −5 °C and below (14.8 kW at the knee
at −5 °C), with 110 rev/s at −10 °C and below. The reason for the strictness
is measured. Below the tank temperature at which the ceiling's duty meets the
condensing limit a step has one duty for its whole full-duty stretch, the one
its cycle delivers at that stretch's mean, while the ceiling's duty falls as
the tank warms. Accepting such a knee, the 15 kW R290 unit with a ceiling of
90 rev/s at −12 and −10 °C outdoor charged the same hour with 17 and 18 %
less time on the limit at 600 s than at 60 s steps, and with 1.1 and 1.0 %
less electricity at 3600 s (heat within 0.05 %).

*Electricity.* Along the stretch on the limit the condensing pressure is the
limit's; only the duty changes, from ``hp_capacity`` down to ``Q_min``. A step
that spends time there solves one more cycle, at the stretch's mean tank
temperature and the limit's duty at it -- which is the stretch's mean duty
exactly, the limit being linear in the tank temperature. That is the midpoint
rule in the one variable that varies, as the full-duty stretch is the midpoint
rule in the tank temperature. Its error is the curvature of the power over the
duty (the power is close to linear in it: 0.33–0.36 W per W from 15 to 5 kW
for the R32 unit) times the spread of the duty inside one step. Measured on a
whole charge from 50 °C, 3600 s against 60 s steps: −0.11 % (ASHPB R32 at
15 °C outdoor), −0.38 % (the same with a frosting coil at 0 °C), −0.58 %
(GSHPB R290), −0.39 % (WSHPB R290). A step that does not reach the cut-out is
solved exactly as with ``"stop"``. One that does costs one cycle per stretch
(two for a step with both, as observed on the four charges above), and the
first such step of a source state also pays for the stretch: the cycle at the
knee, then the search for the smallest duty (usually three solves; more where
a trial the limit refuses is retried half-way back, or where the envelope
refuses the probe from below and the smallest duty is itself searched), then
the cycles at the middle and at the stop. ASHPB and WSHPB keep the result per
source state; GSHPB, whose source is the ground as the step finds it, pays it
in every step that gets to the limit. A stretch that fails at the knee costs
that one cycle and no search, and its steps then cost what ``"stop"`` costs.

*The row.* A step with both stretches reports the run-time-weighted mean of
their two cycles for every rate (heat, power, flow), ``cop_ref`` and
``cop_sys`` as the ratios of those means, and the state quantities
(temperatures, pressures, speed) of the stretch that ran longer
(:func:`tmhp.dynamic_context.merge_running_rows`). ``reduced_duty_fraction
[-]`` is the part of the step run on the limit; ``capacity_clamped``,
``capacity_binding == "condensing_temperature"``, ``Q_max [W]`` (the mean
running duty) and ``Q_request [W]`` are written as on any reduced step, so the
end-of-run summary counts these steps as run below the request.
``reduced_duty_withheld`` is ``None`` on such a row; a row with reduced-duty
charging withheld is the row of ``"stop"`` with that key set (*Where it
applies* above).

*What it does not cover.* Source states in which another limit than the
condensing one governs somewhere between the nameplate cut-out and the stop:
there the charge ends at the nameplate cut-out, as with ``"stop"``, although
``analyze_steady`` and a real inverter unit would go on at the duty that other
limit leaves. Following that duty inside a step (it is not a straight line in
the tank temperature, and it can end abruptly) is left to later work. The
multi-node tanks of GSHPB keep the stop: the
water around their coil has its own cut-out, at which the compressor already
holds it, and the constructor refuses ``"reduce_duty"`` with them. With a
frosting outdoor coil (ASHPB) the limit binds the frost-free cycle and the
delivered heat is its mean over frosting and defrost, still a straight line in
the tank temperature; the frost state is the nameplate's, as on every step.
For the ASHPB, ``analyze_steady`` itself keeps returning a running point for
0.1 to 1.0 K above the charging limit (R32, 15 kW: 63.3 against 63.1 °C at
7 °C outdoor, 62.2 against 61.3 °C at 35 °C): its duty search finds states at
the speed floor that deliver less than the minimum-speed duty by widening the
outdoor coil's approach. The dynamic run stops at the minimum-speed duty
``analyze_steady`` reports as ``Q_min [W]``; for GSHPB and WSHPB the two
coincide (within the 0.05 K the comparison resolved).

Discharge-temperature control (``x_suction_min``, default 0.9; ``None`` switches
it off, ``1`` keeps the superheat stage only) comes first. A running unit whose
discharge runs hot opens its expansion valve and, if that is not enough, lets
the suction go wet or injects liquid; it does not stop. So when the cycle at a
candidate operating point discharges above ``T_dis_max``, the same candidate is
solved again, compressor speed included:

1. with the suction superheat reduced from its solved value -- the set
   ``dT_superheat`` capped by the evaporator approach minus ``dT_hx_min``
   (ASHP, ASHPB, GSHP; GSHPB and WSHPB use the set value) -- towards 0 K, to
   the superheat at which the discharge temperature equals ``T_dis_max``;
2. if 0 K is not enough, with a two-phase suction of quality ``x`` lowered
   towards ``x_suction_min``, to the quality at which it equals the limit;
3. if ``x_suction_min`` is not enough either, as below.

Wet suction is a homogeneous two-phase state at the evaporating pressure
(:func:`tmhp.refrigerant.calc_ref_state`, ``x_suction``): the evaporator outlet
is two-phase, so the evaporator duty is ``m_dot (h_1 - h_4)`` with the wet
``h_1``; the swept mass flow uses the homogeneous density
``1 / (x v_g + (1 - x) v_l)``; the isentropic work is that of the two-phase
state. The cycle balance (condenser = evaporator + refrigerant work) closes
exactly. The default efficiency forms were fitted to dry-suction data; using
them for wet suction is an approximation (its size is discussed below). The quality floor 0.9 is a conservative default
without a quantitative basis, and ``x_suction_min`` overrides it: two-phase
suction is an experimentally used way to hold R32 scroll discharge temperature
(Wang et al., ICEC 2014, paper 1595; Yang et al., Int. J. Refrig. 2015), but no
published lower bound was found, and 10 % liquid by mass is of the order of the
liquid-injection ratios used for this purpose. Running results report
``dT_ref_superheat [K]``, ``x_ref_cmp_in [-]`` and ``discharge_control``
(bool), the same names in all five families; the invariant suite allows a wet
suction only on a controlled row and only down to ``x_suction_min``. Every
discharge comparison uses the tolerance ``DISCHARGE_TOL_K`` = 0.01 K.

The suction state at the limit is not searched with full cycle solves. At
fixed suction and condensing pressure the discharge enthalpy follows from the
suction state and the isentropic efficiency alone, so the suction state whose
discharge is ``T_dis_max`` is first computed from the fluid properties with
the efficiency of the state just solved; the cycle is then solved there, and
the estimate corrected with the efficiency that solve returned (it moves with
the compressor speed, which a denser, wetter suction lowers). Two or three
cycle solves close the control to 1e-5 K of the limit, and a point still above
the limit at ``x_suction_min`` is recognised with one. Root finding on full
cycle solves is kept as the fallback for a fluid without a property state or
a candidate where a proposed suction state has no cycle.

Known limit: no efficiency penalty for wet suction. A controlled point uses
the default efficiency maps unchanged, which is optimistic. Liquid in a
compressor's suction or compression chambers raises the compression power and
lowers the compressor efficiency, by an amount that depends on the heat
exchanged with the liquid and that is partly recovered in practice through
cooler oil and walls (Dutta, Yanagisawa & Fukuta, Int. J. Refrig. 24
(2001) 577--587, for a scroll). No relation between suction quality and
isentropic efficiency was found that could be applied to an unspecified
compressor without a new input, so none is applied. The default maps do not
see a wet suction at all: they take the pressure ratio, the speed and the
evaporating temperature, so a wet suction changes the efficiency only through
the operating point it leads to.

The size of what is missing, for the default R32 15 kW boiler, if the
isentropic efficiency fell with the liquid fraction as
``eta_is (1 - k (1 - x))``, measured with the defaults of the previous release
(the semi-empirical models of 2026-11-15) at the points where the control
went to wet suction with them (the tank temperature is the water entering the
condenser; every point runs at a reduced duty, ``capacity_binding`` in
brackets):

.. list-table::
    :header-rows: 1
    :widths: 34 16 16 16 18

    * - ``cop_ref`` (suction quality), duty delivered of 15 kW
      - ``k = 0`` (model)
      - ``k = 0.5``
      - ``k = 1``
      - ``k = 2``
    * - A−20, tank 60 °C (pressure ratio)
      - 1.469 (0.954), 4.3 kW
      - 1.447 (0.947), 4.3 kW
      - 1.418 (0.938), 4.3 kW
      - 1.310 (0.901), 4.4 kW
    * - A−20, tank 55 °C (pressure ratio)
      - 1.712 (0.981), 7.6 kW
      - 1.705 (0.979), 7.7 kW
      - 1.691 (0.976), 7.7 kW
      - 1.648 (0.964), 7.8 kW
    * - A−15, tank 58 °C (speed ceiling)
      - 1.728 (0.980), 11.2 kW
      - 1.717 (0.977), 11.2 kW
      - 1.703 (0.974), 11.3 kW
      - 1.654 (0.962), 11.3 kW
    * - A−3, tank 58 °C (speed ceiling)
      - 1.700 (0.990), 12.2 kW
      - 1.694 (0.988), 12.2 kW
      - 1.687 (0.987), 12.2 kW
      - 1.661 (0.981), 12.2 kW

So with those defaults the COP of a controlled point was high by a few
percent at most (3.5 % at A−20 against a 60 °C tank for a 10 % efficiency loss
at ``x = 0.9``, 0.8 to 1.4 % where the suction stays at 0.98 to 0.99), and the
same points without a discharge limit ran at ``cop_ref`` 1.430, 1.703, 1.717
and 1.686: the model credits wet suction with up to 2.7 % where a real
compressor loses. The control then acted only at large lifts, where the
pressure-ratio ceiling or the speed ceiling already set the duty, so the
penalty did not reduce what ran at these points. (With the quadratic map of
2026-10-06 and the conductances and tank convention of then, the control acted
already at A7/W65 and A2/W60, and the penalty took duty away: A−3/W58
delivered 12.6 kW with ``k = 0`` and 8.3 kW with ``k = 0.5``.)

The table has not been measured again with the present maps. With them the
control acts more widely: the same boiler delivers its full 15 kW at A−7 and
A−10 against a 55 °C tank and at A−15 against a 50 °C tank, with suction
qualities of 0.96, 0.94 and 0.93. A penalty would therefore act on points
that run at full duty (an inference; not measured).

How each family answers a point the control cannot hold:

* **ASHP, GSHP** — the candidate is rejected inside the operating-point
  search, like a pressure-ratio ceiling. The search moves to a smaller lift
  if one carries the duty; otherwise the capacity search returns the largest
  deliverable duty with ``capacity_clamped == "max"`` and
  ``capacity_binding == "discharge_temperature"``. With no deliverable duty
  the result is the off state with
  ``failure_reason == "discharge_temp_above_max"`` -- unless (ASHP) the
  request is only too *small*: refused at low speed, where the efficiency
  falls and the discharge runs hotter, while a larger duty runs. It is then
  met by on/off cycling at the smallest duty that runs
  (``capacity_clamped == "min"``), as described for the boilers below.

  ASHP decides both questions at the **least-lift point** of a duty — both
  coils at their largest airflow, the smallest pressure ratio and the coolest
  discharge at which the duty is delivered
  (``AirSourceHeatPump._largest_airflow_point``). It is solved as a fixed
  point of the two approach temperatures and is one of the starting points of
  the least-power search, so a band of running approaches thinner than any
  lattice is still entered (about 1 K wide for the default unit at −25 °C). A duty
  whose least-lift point is outside the envelope has no operating point; the
  unit then runs at the largest duty below the request whose least-lift point
  is inside (bisected to 0.1 %), ``capacity_binding`` naming the limit the
  next larger duty runs into — also when a still larger duty would run again,
  which happens because at mid speed the compressor is less efficient and its
  discharge hotter than flat out. As for ASHPB below, the control also acts
  on a candidate whose coils do not carry the cycle, so the coil margins the
  search reads are those of the controlled cycle.
* **ASHPB outdoor-approach search** — a candidate outside the envelope (after
  the control) ranks after every solved candidate, including one short of the
  request at ``rps_max``, and before an unphysical state, a coil that cannot
  carry the duty and no cycle; within that tier it is graded by how far
  outside it is *at the control's last stage* (the suction at
  ``x_suction_min``), so the search moves to the smaller lift that may be
  inside. The control also acts on a candidate whose coil does not carry the
  cycle, so the band of approaches the fan can serve is that of the
  controlled cycle: wet suction adds to the evaporator duty (about 100 W of
  15 kW), which moves the band's lower edge up by a few hundredths of a
  kelvin, and above the band the control runs out at ``x_suction_min``. Read
  from the uncontrolled cycle, the band left a window of running approaches
  that the scan could step over (a request of 15 kW at A-6/W50 was then
  "reduced" to 14.7 kW, and to 8.4 kW one step earlier).
* **ASHPB, GSHPB, WSHPB** (``analyze_steady``) — the solved operating point is
  checked after :func:`tmhp.dynamic_context.unphysical_cycle_detail`. A
  request with no operating point for a reason a smaller duty cures -- the
  discharge limit, the pressure-ratio ceiling, the source heat exchanger's
  range (:func:`tmhp.compressor_envelope.duty_reduction_binding`) -- is retried at
  80/60/40/25/10 % and the boundary is bisected to 1 % of the request; the
  largest duty that runs is returned with ``capacity_clamped == "max"``,
  ``capacity_binding`` naming the limit active at that point -- the
  pressure-ratio ceiling, then the speed ceiling ``rps_max`` (``"compressor"``,
  when the returned point already runs at it), then the limit the next larger
  duty ran into (``compressor_envelope._reduced_binding``; see
  :doc:`/concepts/failure-reason-semantics`), ``Q_max [W]`` and
  ``Q_request [W]``. If none runs the off state carries the request's own
  reason -- except a request refused from *below*, by the discharge limit,
  which is then tried at *larger* duties
  (:func:`tmhp.compressor_envelope.raise_duty_within_envelope`: 5 to 200 % of
  the nameplate from the bottom, the boundary bisected to 0.5 %). At a large
  lift the compressor efficiency falls towards low speed: the discharge runs
  hotter there, past ``T_dis_max`` even at ``x_suction_min``. The smallest
  duty that runs is then the lower end of continuous operation, returned with
  ``capacity_clamped == "min"`` and averaged over the on/off period like a
  minimum-speed row (``Q_min [W]`` is that duty; the loss is the EN 14825
  degradation). The climb passes the rungs refused for the discharge limit
  and ends at the first that is not; if that rung is refused as well, by a
  limit of the other side (the pressure ratio, the heat exchanger, the
  condensing temperature), the duties that run lie between it and the rung
  below and the bisection goes by which side refuses the trial (the 15 kW R32
  boiler at A−20 against a 60 °C tank: 2.1 to 4.3 kW, between the discharge
  limit below and the pressure-ratio ceiling above). The request keeps its
  reason when no duty runs: the two sides meet (the same boiler at A−25
  against a 55 °C tank: the discharge limit refuses the smallest duties and
  the pressure-ratio ceiling every larger one). Of the 2026-11-15 sweep
  (5,796 boiler points, with the efficiency map of then), 73 requests refused
  for the discharge temperature while a larger one ran became cycling rows,
  at a ``Q_min`` of 15--84 % of the nameplate (median 38 %); the 3,803 rows
  that ran before were unchanged.
  The limits are not monotone in the duty together: above the duties
  that run the pressure ratio or the heat exchanger refuses, below them the
  discharge temperature does (at low speed the efficiency falls). So when no
  rung runs, two neighbouring rungs refused for different reasons are
  bisected by reason, which finds a running range narrower than the ladder
  spacing (with the efficiency map of before November 2026, R1234yf
  A-15/W65: 4.0--5.7 kW of a 15 kW request; with the present defaults that
  request runs at 3.3 kW, held by the pressure ratio). Three short
  cuts keep "no duty runs" cheap: a request refused by the pressure ratio,
  or by a coil short at full airflow, has its 10 % rung solved first, and if
  that is refused the same way nothing in between is tried (those limits
  only tighten with the duty); a rung at which every approach has the
  compressor on its speed floor ends the ladder (smaller requests are the
  same cycles); and a request with no cycle at all is not reduced.
* **Boiler dynamic steps** answer the same way. Each step solves its cycle
  once (at the mean tank temperature of its run, see
  :func:`tmhp.dynamic_context.resolve_step_operation`): held by the control
  if possible, otherwise at the largest duty that runs, whose heat then
  sets the run time and the step-average rates (the tank reaches its upper
  bound later); a step with no duty that runs is an off step with the
  reason. The start-temperature retry of that function applies the same
  reduction. A step that needs the reduction costs one cycle solve per
  ladder rung and bisection point: with the efficiency map of before
  November 2026, 24 h of the default R32 boiler on a cold day (outdoor −8 to
  0 °C, dt 600 s; 10 of 22 running steps at a reduced duty, 70 called steps
  with no duty that runs) took about half a minute, against one to two
  seconds on a mild day where the control alone held the limit.

Steady results of every family carry ``efficiency_extrapolated`` (bool):
the point lies outside the data of a default efficiency map (30–110 rev/s and
the hull in pressure ratio and evaporating temperature). Scalar and user
efficiencies give ``False``.

Until November 2026 the envelope had a third limit, an isentropic-efficiency
floor (``ETA_ISEN_MIN`` = 0.30, ``failure_reason == "eta_isen_below_min"``,
``capacity_binding == "compressor_efficiency"``): the BITZER quadratic map,
read outside its data towards low speed, fell to 0.14, and points at which it
was extrapolated and below 0.30 were refused. The floor, its failure reason
and its binding are removed. The maps that ship now are polynomials with no
floor of their own: a nonpositive value makes the point infeasible by the
range rule, and the lower side of the envelope is the discharge-temperature
limit alone.

Effect of the discharge limit and control on the adopted catalogues when they
were introduced (a record of 2026-11-13, with the efficiency map, the
conductances and the tank convention of then; point-weighted COP MAPE; a
point that delivers more than 1 % less than the published duty is not scored,
``capacity_short``):

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    * - Set
      - before (``a1b48df``)
      - limit, no control
      - limit + control (default)
    * - all 15 units
      - 513/513, 11.6 %
      - 473/513, 11.9 %
      - 496/513, 11.7 %
    * - air-to-air (5)
      - 360/360, 12.1 %
      - 360/360, 12.1 %
      - 360/360, 12.1 %
    * - air-to-water (10)
      - 153/153, 10.3 %, bias +5.6 %
      - 113/153, 11.4 %, bias +7.0 %
      - 136/153, 10.5 %, bias +5.9 %
    * - air-to-water, outdoor ≤ −10 °C
      - 30/30, 6.4 %
      - 6/30, 7.3 %
      - 19/30, 4.7 %

(With the ASHPB outdoor-approach band search of #109; the committed
``validation/results`` of then were regenerated with this code.) The control held 30
air-to-water points (2 by superheat alone, 28 with wet suction at
x 0.90–0.99); 23 of them were scored. Of the 17 points not scored, 10 were R32 points whose unbounded
discharge was 243–293 °C: they would have needed suction quality 0.79–0.86,
because the map of then gave eta_is 0.37–0.42 at PR 8.2–12.1 (both the
BITZER map and the ``v2026-09-24`` correlations). The other 7, all R32, ran at
a reduced duty (``capacity_short``, 42–98 % of the published duty) at
x 0.90–0.93. With the present defaults no adopted point stops on the
discharge limit (:doc:`/validation/index`).
