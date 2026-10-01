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
uses 24 L/min. Constant setpoints are independent of the variable strategy's
search interval; reference and constant points are included as interpolation
anchors even if outside that interval. Do not infer an additional hardware
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
the control envelope. A regression demonstrates a feasible PLR 0.3 / 36 L/min
point above 20 K rather than labelling the previous truncated search as a
physical HX capacity failure.

Fan API and migration
---------------------

ASHP accepts ``dV_ou_fan_a_ref/min/max`` and ``dV_iu_fan_a_ref/min/max``;
GSHP accepts the indoor trio; ASHPB accepts ``dV_fan_a_ref/min/max`` [m3/s].
Existing ``*_rated`` inputs remain supported reference aliases; an explicit
``*_ref`` takes precedence. Cross-sectional area and reference fan power are
computed at that fixed reference, independently of the solver limits.

The common HX solver accepts ``dV_fan_ref/min/max``. For compatibility,
omitted limits resolve to 0.05 times reference and reference, respectively.
These are defaults, not an implicit definition of rated as maximum. Set max
explicitly to exceed reference. UA scales with actual/reference airflow and
the fan VSD polynomial evaluates ratios above one without an upper clamp.
Coefficients and their validity require equipment calibration; expanded control
limits do not extend a correlation's proven validity. Polynomial nonnegative
power protection remains independent of any flow reference.

``calc_UA_from_dV_fan`` supports the new ``dV_fan_ref`` keyword and its existing
rated positional argument. Fan-power dictionaries accept ``fan_ref_flow_rate``
and ``fan_ref_power`` first, then their historical rated/design aliases.
HX failures still propagate NaN airflow/power to the cycle optimizer.

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
