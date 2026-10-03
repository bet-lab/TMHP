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
for ground-HX scaling. ``V_cmp_ref`` is swept displacement per revolution;
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
:mod:`tmhp.compressor_efficiency` (frozen coefficients ``v2026-09-24``).
These represent isentropic, volumetric and electro-mechanical efficiency,
respectively. Each input also accepts a scalar, a ``function(PR)`` or a
``function(PR, rps)``. Evaluation validates finite values in (0, 1] and
preserves exceptions raised inside a user function.

``rps_rated`` defines the normalization ``n_star = rps / rps_rated``:
60 rev/s for ASHP/GSHP and 40 rev/s for ASHPB/GSHPB/WSHPB, matching the
source baseline's air-to-air / air-to-water reference speeds. Specify the
same rated speed when comparing model defaults at the same PR and rps.
This reference does not change ``rps_min`` or ``rps_max``. Correlation factors
held above ``n_star=2`` protect extrapolation; they do not impose a speed limit.

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
