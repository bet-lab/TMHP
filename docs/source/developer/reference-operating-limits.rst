Reference points and operating limits
=====================================

Developer naming rules
----------------------

``rated`` means a manufacturer/design reference operating point, not a
maximum operating limit. ``ref`` denotes a fixed normalization point in flow
scaling APIs. Physical/control limits use explicit ``_min``/``_max`` names;
fixed operating commands use ``_constant`` or ``_setpoint``. Ratios state their
denominator, for example ``ground_flow_ref_ratio`` or ``fan_flow_ratio_to_ref``.
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
adapter. Results expose ``ground_flow [m3/s]``, ``ground_flow_ref_ratio``,
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

A constant-speed fan sits at both limits, so both ``fan_flow_min_limit`` and
``fan_flow_max_limit`` (``iu_*`` / ``ou_*``) are true; the direction in which a
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
   (``dT_approach_bounds``, 1--20 K by default) is the only freedom the coil
   has, so it can become the capacity limit: the largest duty is then what the
   coil carries at the upper bound, reported with ``capacity_clamped == "max"``
   and ``capacity_binding == "cycle"``. Finding that ceiling repeats the band
   solve along a ladder of duties and takes 5--10 s per operating point; widen
   the bounds if the coil is meant to run a larger approach.

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
``eta_cmp`` set to ``None`` to the shared factories in
:mod:`tmhp.compressor_efficiency` (frozen coefficients
``BITZER-three-C3-absolute-N-2026-10-06``).
These represent isentropic, volumetric and electro-mechanical efficiency,
respectively. Each input also accepts a scalar, a ``function(PR)`` or a
``function(PR, rps)``. Every evaluated value must satisfy ``0 < eta <= 1``:
a value above one is set to one, a nonpositive or nonfinite value makes the
operating point infeasible, and exceptions raised inside a user function are
preserved.

The BITZER defaults evaluate physical shaft speed ``N = rps`` [rev/s]
directly; ``n_star`` does not normalize the efficiency inputs. ``rps_rated``
is used as a fallback only when a callable is invoked without its speed.
When omitted, the reference state solves the speed delivering ``hp_capacity``
and its ``m_dot_ref_rated`` at the family's rating condition
(:doc:`/models/reference-state`). Explicit rated speed and refrigerant flow
remain available. The reference solve does not change ``rps_min``; an
omitted ``rps_max`` is derived from the rated speed it finds (see the
operating envelope below).

The default polynomials carry their fitting-data box
(``VALIDITY_DOMAIN``: PR 1.50-7.15, N 33.8-72.5 rev/s, from the scroll hull
in the coefficient archive). Above it in PR or speed they hold the boundary
value; below it (PR down to 1, speed down to 15 rev/s) they continue along the
boundary slope only where that lowers the efficiency.
``efficiency_extrapolated(model, PR, rps)`` reports whether a point lies
outside the box; ``model.raw(PR, rps)`` is the unguarded polynomial.

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

The source module SHA, original checkout commit, Notion report and unchanged
coefficient snapshot are recorded under ``docs/audits/compressor-efficiency-*``.
The report's tuned low-speed penalties are not used. This baseline models
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
      - Single-stage scroll hardware limit. With the discharge limit in
        force it is a sanity bound: inside the discharge limit only R134a /
        R1234yf boiler points at PR 11–15.4 used it, every one outside the
        efficiency fit in both pressure ratio and speed. Higher ratios are
        two-stage or injection machines.
    * - ``rps_max``
      - ``None``: ``max(min(2 × rps_rated, 120), 1.5 × rps_rated)``.
      - The rated speed is solved first over ``[rps_min, 150]``. The adopted
        catalogues need at most 1.66 × the rated speed; the former fixed
        150 rev/s let the compressor alone deliver 2.5–5.9 × nameplate.
        120 rev/s is the top of variable-speed scroll and rotary ranges; a
        machine rated above 80 rev/s (R134a with the default displacement,
        #86) keeps 1.5 × its rated speed, the catalogue maximum-to-rated
        capacity ratio. A user value is kept.
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
  ``hp_capacity / UA_tank_hx`` -- 59.9 °C for R410A with the default 5 K
  approach, 60.0 °C for R32, 69.9 °C for R290;
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
R134a, which is why GSHPB and WSHPB default to R290. The duty reduction above
then serves ``analyze_steady`` and the steps that go past the cut-out all the
same: a stratified tank whose coil-side water is hotter than the sensor, a
minimum run time, a set point moved after construction.

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
exactly. The efficiency maps were fitted to dry suction; using them for wet
suction is an approximation (its size is discussed below). The quality floor 0.9 is a conservative default
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
the dry-suction efficiency maps unchanged, which is optimistic. Liquid in a
scroll's suction or compression pockets raises the compression power and
lowers the compressor efficiency, by an amount that depends on the heat
exchanged with the liquid and that is partly recovered in practice through
cooler oil and scroll walls (Dutta, Yanagisawa & Fukuta, Int. J. Refrig. 24
(2001) 577--587). No relation between suction quality and isentropic
efficiency was found that could be applied to an unspecified compressor
without a new input, so none is applied. The size of what is missing, for the
default R32 15 kW boiler, if the isentropic efficiency fell with the liquid
fraction as ``eta_is (1 - k (1 - x))``:

.. list-table::
    :header-rows: 1
    :widths: 34 16 16 16 18

    * - Quantity
      - ``k = 0`` (model)
      - ``k = 0.5``
      - ``k = 1``
      - ``k = 2``
    * - ``eta_is`` at ``x = 0.9``
      - unchanged
      - −5 %
      - −10 %
      - −20 %
    * - A7/W65 15 kW: ``cop_ref`` (suction quality)
      - 2.307 (0.961)
      - 2.278 (0.956)
      - 2.241 (0.949)
      - 2.096 (0.920)
    * - A2/W60 15 kW: ``cop_ref`` (suction quality)
      - 2.283 (0.952)
      - 2.250 (0.946)
      - 2.205 (0.937)
      - 2.037 at 14.3 kW (0.900)
    * - A0/W60: duty delivered of 15 kW
      - 15.0 kW
      - 15.0 kW
      - 13.3 kW
      - none (off)
    * - A−3/W58: duty delivered of 15 kW
      - 12.6 kW
      - 8.3 kW
      - none (off)
      - none (off)
    * - Mild day (A1–13, 21 of 144 steps controlled): daily COP
      - 2.484
      - 2.474
      - 2.461
      - 2.451

So the COP of a controlled point is high by a few percent at most (2.8 % at
A7/W65 for a 10 % efficiency loss at ``x = 0.9``; 0.9 % over the mild day),
and the same point uncontrolled, without a discharge limit, is 2.288: the
model credits wet suction with 0.8 % where a real compressor loses. The
larger effect is on what runs. A less efficient wet compression discharges
hotter, so the control needs a wetter suction and reaches ``x_suction_min``
sooner; the duty the model reports at the edge of the discharge limit, more
than its COP, is an upper estimate.

How each family answers a point the control cannot hold:

* **ASHP, GSHP** — the candidate is rejected inside the operating-point
  search, like a pressure-ratio ceiling. The search moves to a smaller lift
  if one carries the duty (ASHP adds a 16 × 16 approach lattice when its 8 × 8
  one finds nothing: the band between the coil's airflow limit and the
  discharge limit can be a few kelvin wide); otherwise the capacity search
  returns the largest deliverable duty with ``capacity_clamped == "max"`` and
  ``capacity_binding == "discharge_temperature"`` (the next larger duty is
  deliverable once the limit is lifted). With no deliverable duty the result
  is the off state with ``failure_reason == "discharge_temp_above_max"``.
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
  range, the efficiency floor
  (:func:`tmhp.compressor_envelope.duty_reduction_binding`) -- is retried at
  80/60/40/25/10 % and the boundary is bisected to 1 % of the request; the
  largest duty that runs is returned with ``capacity_clamped == "max"``,
  ``capacity_binding`` naming the limit (see
  :doc:`/concepts/failure-reason-semantics`), ``Q_max [W]`` and
  ``Q_request [W]``. If none runs the off state carries the request's own
  reason. The limits are not monotone in the duty together: above the duties
  that run the pressure ratio or the heat exchanger refuses, below them the
  discharge temperature does (at low speed the efficiency falls). So when no
  rung runs, two neighbouring rungs refused for different reasons are
  bisected by reason, which finds a running range narrower than the ladder
  spacing (R1234yf A-15/W65: 4.0--5.7 kW of a 15 kW request). Three short
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
  ladder rung and bisection point: 24 h of the default R32 boiler on a cold
  day (outdoor −8 to 0 °C, dt 600 s; 10 of 22 running steps at a reduced
  duty, 70 called steps with no duty that runs) takes about half a minute,
  against one to two seconds on a mild day where the control alone holds the
  limit.

Steady results of every family carry ``efficiency_extrapolated`` (bool):
the point lies outside the (PR, N) box of a default efficiency fit. Scalar
and user efficiencies give ``False``.

Isentropic-efficiency floor (``ETA_ISEN_MIN`` = 0.30). With the discharge
control a point can hold ``T_dis_max`` while the shipped isentropic fit, read
outside its data, is far below anything measured: the guard's first-order
continuation towards 15 rev/s reaches 0.14. Inside the convex hull of its
3899 data points the fit never goes below 0.336, and its lowest value over the
whole fitted box is 0.297 (corner PR 7.15, 33.8 rev/s). A running point at
which the shipped fit is extrapolated *and* below 0.30 is therefore outside the
envelope (``failure_reason == "eta_isen_below_min"``; ASHP and GSHP reject
the candidate). The values held at the high-PR and high-speed boundaries are
0.306 or more, so the rule bites only in the low-speed continuation. Scalar
and user efficiencies are the caller's statement and are not checked: the
floor applies only where a shipped map is extrapolated. Of
the 2026-11-13 sweep, before this rule, 23 running rows (20 of them under
discharge control) were at eta_is 0.25–0.30, all at PR 6.4–6.9 and
15–21 rev/s; no adopted catalogue point is below 0.37. The rule is independent
of ``T_dis_max``. A rising minimum speed with pressure ratio would refuse the
same corner but needs envelope data the library does not have; the floor uses
only the fit's own data range.

Effect on the adopted catalogues (2026-11-13, point-weighted COP MAPE; a
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
``validation/results`` were regenerated with this code.) The control holds 30
air-to-water points (2 by superheat alone, 28 with wet suction at
x 0.90–0.99); 23 of them are scored. Of the 17 points not scored, 10 are R32 points whose unbounded
discharge is 243–293 °C: they would need suction quality 0.79–0.86, because
the fitted eta_is is 0.37–0.42 at PR 8.2–12.1 (both in the default and the
``v2026-09-24`` correlations). The other 7, all R32, run at a reduced duty
(``capacity_short``, 42–98 % of the published duty) at x 0.90–0.93.
