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
        dV_b_f_lpm=80,
        ground_flow_control="optimal_power",
        hydraulic_pump=True, pump_efficiency=0.6,
        variable_Rb=True, variable_ground_hx_UA=True,
        m_dot_ref_rated=0.04,
        ground_flow_min_ratio=0.2, ground_flow_max_ratio=1.2,
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
---------------------------------------------

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

``analyze_steady(..., ground_flow_ratio=f)`` evaluates a prescribed candidate
within the configured bounds and skips the flow optimizer. This supports
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
  2H. Branch pressure losses are not added in series. ``dp_common=0`` excludes
  header/manifold/minor losses. A nonzero value is a prescribed pressure loss
  at the evaluated point, not a resolved pipe network. Constant pump efficiency
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
