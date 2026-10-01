# Reference / rated / max audit — TMHP #67

A = physical state/reference; B = normalization; C = command; D = limit.
All identifiers matching ref/rated/design/ratio/min/max in the requested 10 modules,
plus the actual-flow borehole helper, are inventoried from the Python AST. Thermodynamic
`ref` names denote refrigerant, while flow `*_ref` inputs denote fixed normalization.
This inventory records those existing naming exceptions rather than silently reinterpreting them.
Line references and source hashes are in `reference-rated-max.json`.

## Reviewed behavior

- Ground: optimizer uses actual m³/s; reference and constant command are independent. Rb* uses actual branch kg/s, with reference/setpoint anchors. Pump physics uses actual flow. Ground UA permits ratio > 1 with fixed denominators.
- Fans: common HX solver uses independent min/max; ASHP both units, ASHPB outdoor and GSHP indoor pass those inputs through. UA and VSD power allow >1 reference ratio. Rated/design inputs remain reference aliases.
- Compressor: V_cmp_ref is swept displacement, not a speed bound. Explicit rps_min/rps_max bracket the solver; no implicit reference-speed ceiling was found.
- WSHPB: fixed source-water and tank UA, actual source flow, explicit mixing/speed/pressure/tank/time limits; no air fan or hidden rated-flow ceiling to expand.
- Retained clamps: nonnegative polynomial power; physical compressor PR/speed constraints; temperature validity bounds; ULP-only interpolation/search endpoint rounding. No rated-UA cap or general ratio ≤1 clamp exists.
- Compatibility: old ground constructor/steady ratio inputs emit DeprecationWarning and map to the fixed reference. Rated fan inputs remain valid physical references; design aliases remain supported. Defaults preserve the old search envelope unless explicit new limits are supplied.

## Identifier inventory

| File | Identifier | Role | Interpretation |
| --- | --- | --- | --- |
| air_source_heat_pump.py | `E_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `E_ou_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `PR_cycle_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `PR_cycle_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `Q_ref_cond` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `Q_ref_evap` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `Q_ref_iu` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `Q_ref_ou` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `UA_cond_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `UA_cond_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `UA_evap_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `UA_evap_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `UA_iu_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `UA_ou_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `V_cmp_ref` | A | Swept displacement per revolution; speed bounds are rps_min/rps_max. |
| air_source_heat_pump.py | `_optimize_operation` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| air_source_heat_pump.py | `calc_ref_state` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `dP_iu_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dP_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dP_ou_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dP_ou_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dT_hx_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `dT_ref_cond` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `dT_ref_evap` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `dV_iu_fan_a_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dV_iu_fan_a_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `dV_iu_fan_a_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `dV_iu_fan_a_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dV_iu_fan_a_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dV_ou_fan_a_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dV_ou_fan_a_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `dV_ou_fan_a_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `dV_ou_fan_a_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `dV_ou_fan_a_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `eta_iu_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `eta_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `eta_ou_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `eta_ou_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump.py | `h_ref_cmp_out_isen` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `ratio_P_cmp` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| air_source_heat_pump.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `E_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `PR_cycle_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `PR_cycle_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `Q_ref_cond` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `Q_ref_ou` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `Q_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `Q_ref_tank_calc` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `UA_cond_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `UA_evap_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `UA_ou_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `V_cmp_ref` | A | Swept displacement per revolution; speed bounds are rps_min/rps_max. |
| air_source_heat_pump_boiler.py | `X_in_ref_ou` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `_optimize_operation` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| air_source_heat_pump_boiler.py | `calc_ref_state` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `dP_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `dP_ou_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `dT_hx_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `dT_ref_ou` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `dT_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `dV_fan_a_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `dV_fan_a_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `dV_fan_a_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `dV_fan_a_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `dV_mix_w_out_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `dV_ou_fan_a_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `eta_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `eta_ou_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| air_source_heat_pump_boiler.py | `h_ref_cmp_out_isen` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `ratio_P_cmp` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| air_source_heat_pump_boiler.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| air_source_heat_pump_boiler.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| air_source_heat_pump_boiler.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `E_cmp_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `E_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `PR_cycle_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `PR_cycle_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `Q_cond_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `Q_evap_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `Q_ref_cond` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `Q_ref_evap` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `Q_ref_iu` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `UA_cond_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `UA_evap_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `UA_ground_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `UA_iu_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `V_cmp_ref` | A | Swept displacement per revolution; speed bounds are rps_min/rps_max. |
| ground_source_heat_pump.py | `_optimize_operation` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump.py | `_rated_hx_UAs` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `calc_ref_state` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `dP_iu_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `dP_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `dT_hx_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `dT_ref_cond` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `dT_ref_evap` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `dV_iu_fan_a_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `dV_iu_fan_a_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `dV_iu_fan_a_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `dV_iu_fan_a_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `dV_iu_fan_a_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `eta_iu_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `eta_iu_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `ground_flow_max_lpm` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `ground_flow_max_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_source_heat_pump.py | `ground_flow_min_lpm` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `ground_flow_min_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_source_heat_pump.py | `ground_flow_ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump.py | `ground_flow_ref_lpm` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `m_dot_ref_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump.py | `maximum` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump.py | `ratio_P_cmp` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump.py | `t_max_s` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `PR_cycle_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `PR_cycle_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `Q_ref_ground` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `Q_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `UA_cond_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump_boiler.py | `UA_evap_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump_boiler.py | `V_cmp_ref` | A | Swept displacement per revolution; speed bounds are rps_min/rps_max. |
| ground_source_heat_pump_boiler.py | `_optimize_operation` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump_boiler.py | `calc_ref_state` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `dT_hx_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `dT_ref_ground` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `dT_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `dV_mix_w_out_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `ground_flow_max_lpm` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `ground_flow_max_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_source_heat_pump_boiler.py | `ground_flow_min_lpm` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `ground_flow_min_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_source_heat_pump_boiler.py | `ground_flow_ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump_boiler.py | `ground_flow_ref_lpm` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump_boiler.py | `h_ref_cmp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `h_ref_cmp_out` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `h_ref_cmp_out_isen` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `h_ref_exp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `h_ref_exp_out` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `m_dot_ref_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_source_heat_pump_boiler.py | `ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump_boiler.py | `ratio_P_cmp` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_source_heat_pump_boiler.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `rho_ref_cmp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_source_heat_pump_boiler.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_source_heat_pump_boiler.py | `t_max_s` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `PR_cycle_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `PR_cycle_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `P_ref_cond_sat` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `P_ref_evap_sat` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `Q_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `Q_ref_tank_actual` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `Q_ref_water` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `UA_cond_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| water_source_heat_pump_boiler.py | `UA_evap_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| water_source_heat_pump_boiler.py | `V_cmp_ref` | A | Swept displacement per revolution; speed bounds are rps_min/rps_max. |
| water_source_heat_pump_boiler.py | `_optimize_operation` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| water_source_heat_pump_boiler.py | `calc_ref_state` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `dT_hx_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `dT_ref_tank` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `dT_ref_water` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `dV_mix_w_out_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `h_ref_cmp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_cmp_out` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_cmp_out_isen` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_cond_sat_l` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_evap_sat` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_exp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `h_ref_exp_out` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `ratio_P_cmp` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| water_source_heat_pump_boiler.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `rho_ref_cmp_in` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| water_source_heat_pump_boiler.py | `s_ref_cond_sat_l` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `s_ref_evap_sat` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| water_source_heat_pump_boiler.py | `t_max_s` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| hx_fan.py | `fan_design_flow_rate` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| hx_fan.py | `fan_design_power` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `Q_ref_target` | C | Actual control command/target; never a denominator. |
| heat_exchanger.py | `T_ref_cond_sat_l_K` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| heat_exchanger.py | `T_ref_evap_sat_K` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| heat_exchanger.py | `T_ref_sat` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| heat_exchanger.py | `T_ref_sat_K` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| heat_exchanger.py | `UA_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `UA_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `dV_fan_design` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `dV_fan_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `dV_fan_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `dV_fan_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `dV_fan_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `dV_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `dV_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `err_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `err_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `m_dot_fluid_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `m_dot_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `m_dot_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| heat_exchanger.py | `m_dot_ref_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `maximum` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `minimum` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| heat_exchanger.py | `rated_capacity` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `reference` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| heat_exchanger.py | `resistance_ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_loop.py | `UA_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_loop.py | `default_ref_lpm` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_loop.py | `legacy_ref_lpm` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_loop.py | `m_dot_ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_loop.py | `m_dot_ref_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_loop.py | `max_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_loop.py | `min_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| ground_loop.py | `ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_loop.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| ground_loop.py | `volume_flow_constant` | C | Actual control command/target; never a denominator. |
| ground_loop.py | `volume_flow_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_loop.py | `volume_flow_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| ground_loop.py | `volume_flow_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_loop.py | `volume_flow_ref` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| ground_flow_control.py | `prescribed_ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_flow_control.py | `ratio` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
| ground_flow_control.py | `ref` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| compressor_speed.py | `hp_capacity` | A | Physical thermodynamic/refrigerant state or equipment quantity; ref in refrigerant names is not normalization. |
| compressor_speed.py | `res_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| compressor_speed.py | `res_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| compressor_speed.py | `rps_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| compressor_speed.py | `rps_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| borehole.py | `m_flow_max` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| borehole.py | `m_flow_min` | D | Explicit control/hardware, physical capacity, validity or time bound; independent of reference. |
| borehole.py | `m_flow_rated` | A/B | Fixed physical reference; normalization where used in UA/fan scaling. Not a limit. |
| borehole.py | `max_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| borehole.py | `min_ratio` | D (legacy) | Deprecated control-bound adapter; new internals use independent absolute flow limits. |
| borehole.py | `ratios` | B | Ratio has fixed physical denominator; no automatic upper clamp at one. Legacy ground ratio bounds map to absolute limits. |
