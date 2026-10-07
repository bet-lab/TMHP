Coupled ground-loop flow
========================

GSHP and GSHPB offer an opt-in ground-loop model. The default remains constant
flow, prescribed pump power, fixed UA and fixed effective borehole resistance.
Multi-borehole normalization is corrected in **all** modes: the signed
field-total extraction is divided by total active length, ``N_1 * N_2 * H_b``.
This intentionally changes legacy multi-borehole temperatures.

Example
-------

.. code-block:: python

    from tmhp import GroundSourceHeatPump

    model = GroundSourceHeatPump(
        ref="R410A", hp_capacity=8000, V_cmp_ref=1.2e-5,
        UA_ground_rated=2000, UA_iu_rated=2000,
        N_1=2, N_2=2, H_b=100, B=6, Ts=16,
        ground_flow_ref_lpm=80, ground_flow_constant_lpm=80,
        ground_flow_min_lpm=16, ground_flow_max_lpm=96,
        ground_flow_control="optimal_power",
        hydraulic_pump=True, pump_efficiency=0.6,
        variable_Rb=True, variable_ground_hx_UA=True,
        m_dot_ref_rated=0.04,
    )
    row = model.analyze_steady(
        Q_r_iu=-4000, T0=7, T_a_room=20, T_bhe_wall=16,
    )
    if row["converged"]:
        print(row["ground_flow_ratio"], row["cop_sys [-]"])

These are illustrative model inputs, not a calibrated manufacturer's product.
The default search bounds (0.2–1.2), UA resistance fractions (0.5/0.3/0.2) and
flow exponents (0.8/0.8) are explicit modelling assumptions, not equipment limits
or validated refrigerant correlations. Supply values appropriate to the case.

Physical HX sizing and compressor efficiencies
----------------------------------------------

GSHP sizes its physical ground HX with
``UA_ground_rated = ground_hx_ua_per_capacity * hp_capacity``. The default
coefficient is 0.18 K^-1: an 8 kW unit therefore uses 1440 W/K. An explicit
``UA_ground_rated`` overrides this sizing rule. The independent air-side
``UA_iu_rated`` defaults to 0.08 times rated capacity, or 640 W/K at 8 kW.
Both physical UAs remain attached to the same hardware in cooling and heating.
The deprecated ``UA_cond``/``UA_evap`` inputs retain their historical cycle-role
mapping only when explicitly supplied; physical inputs take precedence.

The 0.18 coefficient is a reduced-order engineering sizing assumption, not
an empirical correlation from Longo (2009). Approximately 3 L/min/kW water
flow, 1.2 times rated capacity as HX duty and a 4 K leaving approach give
``UA = Cw * ln(1 + Qhx / (Cw * approach))``, approximately 0.186 times
rated capacity. The rounded default requires equipment-specific calibration.
Longo's DOI 10.1016/j.expthermflusci.2008.09.004 supports only qualitative
mass-flux dependence of plate-HX condensation, not the capacity coefficient.

GSHP now defaults to ``eta_v=0.9`` and ``eta_em=0.8``. Volumetric efficiency
sets speed through ``m_dot = eta_v * displacement * speed * suction_density``.
Electrical compressor input is ``E_cmp = E_cmp_ref / eta_em``; refrigerant
heat balances use the gas compression work ``E_cmp_ref``. Motor losses
``E_cmp_loss`` are outside the refrigerant cycle. System COP and the flow
objective include the electrical input. These defaults intentionally change
previous results; explicitly set both efficiencies to 1 to reproduce the
previous ideal-efficiency convention with otherwise identical parameters.

Control and boundaries
----------------------

``ground_flow_control="constant"`` evaluates rated water flow. ``optimal_power``
compares total electrical power at the same requested load: compressor + pump
+ indoor fan for GSHP, and compressor + pump for GSHPB. It always uses
hydraulic pump power, even if ``hydraulic_pump`` is not set. The convenience
flag ``variable_ground_flow=True`` selects ``optimal_power``. Variable UA and
variable resistance are separate opt-ins. A fair control comparison enables
the same component physics in both constant and optimal cases.

``analyze_steady(..., ground_flow_lpm=actual_lpm)`` evaluates a prescribed candidate
within the explicit actual-flow bounds and skips the flow optimizer. This supports
objective curves and independent optimum checks. GSHP's inner solver minimizes
compressor + pump + indoor-fan power over the indoor approach, subject to ground
HX duty equality. Its outer solver also compares ``E_tot [W]``, including the
indoor fan. ``E_cmp_plus_pmp [W]`` is retained only as a component diagnostic.
System COP is delivered indoor heat/cooling duty divided by ``E_tot [W]``.
The room temperature supplied to ``analyze_steady`` is used by both the cycle
and the indoor HX and appears unchanged in the output. Omitting it uses the
constructor's ``T_a_room``; candidate evaluations do not overwrite this setting.

For GSHP, ``T_bhe_wall`` fixes the wall temperature for the steady snapshot;
its default is the model's current wall temperature (initially ``Ts``).
For GSHPB, ``T_source`` retains its existing meaning as prescribed BHE outlet
fluid temperature. Supply ``T_bhe_wall`` to close the borehole wall/fluid balance
and make flow-dependent Rb* affect the cycle. Without it, the fixed source-fluid
boundary is retained, and Rb* affects the inferred wall temperature only.

In dynamic simulations, each candidate uses the current-time ground response
without committing a load pulse. Only the selected operating point updates
history. An external GSHPB ground coupler must provide the optional
``preview_wall_temperature_rise(n, time_arr, q_unit)`` method to use the new
coupled flow mode; legacy couplers still work with the default model.

Module boundaries and assumptions
---------------------------------

* ``g_function`` and ``ground_coupling`` describe ground-to-wall response.
  Loads are extraction-positive W/m; cooling heat rejection is negative.
* ``borehole`` describes wall-to-fluid resistance in m K/W. The existing
  local-resistance and axial-correction model is preserved. ``variable_Rb``
  precomputes 101 flow-grid values, including rated flow, and uses PCHIP with
  no extrapolation. A fixed user ``R_b`` override cannot be combined with it.
* ``pump`` uses Darcy–Weisbach with laminar/Haaland friction. Identical U-tubes
  are in parallel: branch flow is total flow / borehole count; pipe length is
  2H. Branch pressure losses are not added in series. Auxiliary HX, header,
  valve, strainer and common-pipe loss is specified by ``dp_aux_ref`` [Pa] at
  ``ground_flow_ref_lpm`` and scales with flow ratio to ``dp_aux_exponent``
  (default 2.0). Zero reference loss preserves BHE-only behavior.
  ``dp_common`` is a deprecated reference-loss alias, with a migration warning;
  it is never an absolute operating-point constant. Low-level pump calls with
  nonzero auxiliary loss require ``volume_flow_ref`` [m³/s]. Constant pump efficiency
  is assumed; pump electrical input heats the water as in the legacy model.
  ``pipe_inner_diameter`` defaults to 2*r_in and must match it if supplied.
* ``heat_exchanger`` uses a constant-temperature refrigerant-side approximation:
  effectiveness = 1 - exp(-UA/(m cp)). UA is the inverse sum of water-side,
  refrigerant-side and constant rated resistances. ``m_dot_ref_rated`` is required
  for variable UA; it must come from a documented reference cycle or rating.
  More flow can increase conductance while reducing effectiveness.
* ``ground_loop`` owns field units and component settings. ``ground_flow_control``
  provides the ground-specific closure, root and flow search. System classes
  retain the cycle, load-side control and tank orchestration.

The thermophysical water properties remain the library's constant values.
Detailed HX geometry, header networks, compressor-model refactoring and field
measurement validation are outside this change. Existing public functions in
``enex_functions``, ``hx_fan``, ``g_function`` and ``heat_transfer`` remain imports
of the corresponding implementations.

Feasibility and reporting
-------------------------

The coupled solver requires ground HX duty equality (absolute tolerance 0.01 W
or relative tolerance 1e-5), source temperature closure (1e-5 K), valid cycle
states, compressor speed/pressure limits and delivery of the requested load.
The ground approach is searched over the existing 1–20 K interval. Valid
neighbouring samples bracket a root; invalid cycle points are never treated
as valid residuals. Flow selection compares a coarse grid, the bounds, rated
flow if in range, and a bounded scalar refinement. This numerical search is
checked against explicit flow sweeps; it is not a proof of a global minimum
for arbitrary disconnected feasible domains.

Inspect ``converged``, ``hx_feasible`` and ``failure_reason``. In the new mode,
all-infeasible cases return zero delivered heat and operating power, NaN COP
and NaN selected flow. Diagnostics retain attempted HX available/required
heat and capacity ratio. Compressor load limits are distinguished from
``ground_hx_capacity_insufficient``. This stricter behavior is opt-in; old
legacy diagnostic behavior is unchanged.

Results include ``ground_flow_ratio``, ``dV_bhe_f [m3/s]``,
``m_dot_borehole [kg/s]``, ``R_b_eff [mK/W]``, ``UA_ground [W/K]``,
``E_cmp_plus_pmp [W]``, ``flow_optimizer_success``, ``flow_optimizer_nfev``,
``flow_bound_active``, ``Q_HX_available [W]``, ``Q_ref_required [W]``,
``hx_capacity_margin [W]``, ``hx_capacity_ratio``, ``approach_solver_success``
and ``approach_at_bound``. ``flow_optimizer_success=False`` with zero evaluations
means optimization was not run in constant/prescribed-flow mode.

For resistance units and flow conventions, see the
`pygfunction pipe documentation <https://pygfunction.readthedocs.io/en/stable/modules/pipes.html>`_.

Reference flow is independent of limits
---------------------------------------

The new API separates ``ground_flow_ref_lpm`` (fixed normalization),
``ground_flow_constant_lpm`` (constant command) and ``ground_flow_min_lpm`` /
``ground_flow_max_lpm`` (search limits). Increasing the maximum never changes
the water denominator, reference UA or reference/setpoint Rb* anchors. UA and
fan power may exceed reference values. See :doc:`../developer/reference-operating-limits`
for full migration, fan limits and compatibility defaults.


Auxiliary pressure drop and operating limits
--------------------------------------------

.. math::

   \Delta p_{total}(\dot V) = \Delta p_{BHE}(\dot V)
     + \Delta p_{aux,ref}(\dot V/\dot V_{ref})^{n_{\Delta p}}

   E_{pmp} = \Delta p_{total}\dot V / \eta_{pmp}

``dp_aux_ref=0.0`` and ``dp_aux_exponent=2.0`` are shared GSHP/GSHPB defaults.
A nonzero auxiliary loss activates hydraulic pump evaluation. Reference flow
is independent of the maximum; ratios above one are supported. BHE pressure
uses branch flow and auxiliary pressure uses field-total flow. Diagnostics
``ground_pressure_drop_bhe [Pa]``, ``ground_pressure_drop_aux [Pa]`` and
``ground_pressure_drop_total [Pa]`` expose each contribution; the existing
``ground_pressure_drop [Pa]`` remains an alias for the total.

The constant-flow setpoint must lie within the stated minimum/maximum,
including direct controller calls. An invalid setpoint raises ``ValueError``;
it is not silently clamped. Valid constant control uses the same setpoint at
every active load, and zero flow/power is reserved for off or failed points.

Generic ASHRAE fan control enforces 15--100% of the fixed reference
flow. Hardware limits may narrow this interval; explicit custom fan curves
permit independently declared ranges. The unchanged empirical power curve
has no added electrical floor. Below-minimum duty clamps the airflow but
remains thermally infeasible until the indoor approach closes the heat duty.
A failed point is excluded from plots and savings, rather than interpreted
as zero-power operation. See :doc:`../developer/reference-operating-limits`.

ASHRAE 2024 Systems and Equipment Chapter 44, Hydronic System Curves, supports
approximately quadratic system resistance. ASHRAE 2023 Applications SI
Chapter 35, Table 8, provides closed-loop GSHP pumping/head guidelines. These
are modeling guidance, not measured loss data for any specific installation.
Static borehole elevation head is not added to this closed-loop model.

ASHRAE pressure-budget calibration
----------------------------------

``tmhp.pump.calc_aux_loss_from_ashrae_grade`` derives equivalent auxiliary
resistance from grade A-D and an explicit total reference flow, borehole
geometry and fluid properties. Its single source is Kavanaugh & Rafferty
(2014), ASHRAE, *Geothermal Heating and Cooling: Design of Ground-Source
Heat Pump Systems*, Table 6.2, p. 185. The finite upper boundaries are
140, 210, 280 and 420 kPa. Grade F has no finite upper boundary.

The helper subtracts one parallel branch's straight-pipe pressure loss
from the budget. Pass its ``dp_aux_ref`` to the heat pump with the same
``ground_flow_ref_lpm`` and ``dp_aux_exponent=2``. ``K_aux`` is based on
total flow in the auxiliary pipe; ``K_aux_branch`` uses branch flow.
These are equivalent loop resistances, not measured fitting coefficients.

The source table assumes 3 L/min/kW and 70% hydraulic efficiency. Applying
its pressure boundary at a caller-selected flow is an explicit modeling
assumption, not a pumping-grade certification. The helper does not change
reference flow, heat-exchanger UA or the electrical pump performance map.
