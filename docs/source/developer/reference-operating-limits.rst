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
Omitting all new inputs preserves historical defaults (GSHP 20.04 L/min,
GSHPB 24 L/min; ratio interval 0.2–1.2). ``dV_b_f_m3s`` remains a reference-flow
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
from airflow limits. Defaults remain 1–20 K. An expanded water-flow interval can
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
WSHPB has fixed surface-water/tank UAs and explicit speed limits; it contains
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

How each family answers a point above ``T_dis_max``:

* **ASHP, GSHP** — the candidate is rejected inside the operating-point
  search, like a pressure-ratio ceiling. The search moves to a smaller lift
  if one carries the duty (ASHP adds a 16 × 16 approach lattice when its 8 × 8
  one finds nothing: the band between the coil's airflow limit and the
  discharge limit can be a few kelvin wide); otherwise the capacity search returns the largest
  deliverable duty with ``capacity_clamped == "max"`` and
  ``capacity_binding == "discharge_temperature"`` (the next larger duty is
  deliverable once the limit is lifted). With no deliverable duty the result
  is the off state with ``failure_reason == "discharge_temp_above_max"``.
* **ASHPB, GSHPB, WSHPB** (``analyze_steady``) — the solved operating point is
  checked after :func:`tmhp.dynamic_context.unphysical_cycle_detail`. A
  request above the limit is retried at 80/60/40/25/10 % and the boundary is
  bisected to 1 % of the request; the largest duty that runs is returned with
  ``capacity_clamped == "max"``, ``capacity_binding ==
  "discharge_temperature"``, ``Q_max [W]`` and ``Q_request [W]``. If none runs
  the off state carries ``discharge_temp_above_max``. The discharge
  temperature is not monotone in the duty (at low speed the efficiency
  falls), so this is the boundary nearest the request.
* **Boiler dynamic steps** are off steps with ``discharge_temp_above_max``;
  the step does not reduce its duty.

Steady results of every family carry ``efficiency_extrapolated`` (bool):
the point lies outside the (PR, N) box of a default efficiency fit. Scalar
and user efficiencies give ``False``.

The low-speed, high-pressure-ratio corner (PR > 7.15 below 33.8 rev/s, where
the guarded fit continues down to eta_is 0.14) gets no separate speed floor:
all 85 running points of the 2026-11-13 sweep in that corner discharge at
191–348 °C and are refused by the discharge limit.

Effect on the adopted catalogues (2026-11-13, point-weighted COP MAPE; the
committed ``validation/results`` are regenerated separately):

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    * - Set
      - before (``a1b48df``)
      - this envelope
      - envelope, ``T_dis_max=None``
    * - all 15 units
      - 513/513, 11.6 %
      - 475/513, 11.9 %
      - 507/513, 11.6 %
    * - air-to-air (5)
      - 360/360, 12.1 %
      - 360/360, 12.1 %
      - 360/360, 12.1 %
    * - air-to-water (10)
      - 153/153, 10.3 %, bias +5.6 %
      - 115/153, 11.3 %, bias +7.0 %
      - 147/153, 10.3 %
    * - air-to-water, outdoor ≤ −7 °C
      - 57/57
      - 23/57
      - 51/57

The 38 air-to-water points refused discharge at 141–293 °C in the unbounded
model (R32 21, R410A 11, R290 6); the six that ``T_dis_max=None`` still loses
are refused by the pressure-ratio ceiling *during the outdoor-approach search*
although their solved ratio is 10.0–10.5 -- the ASHPB search has no pre-scan
(#96 C2) -- and all six exceed the discharge limit anyway.

Not modelled: discharge-temperature *control* (lower superheat, wet suction,
liquid or vapour injection). Evaluated on the solved states, it would
recover 25 of the 40 adopted air-to-water catalogue points above the limit
(capacity −4 %, COP −0.7 %) and none of the R32 points at 221–293 °C, whose
discharge temperature comes from the fitted eta_is of 0.37–0.45 at
PR > 7 (both the default and the ``v2026-09-24`` correlations fall there).

