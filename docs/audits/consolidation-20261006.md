# TMHP consolidation — 2026-10-06

All five auxiliary TMHP worktrees were archived, verified and removed. Only `/home/habin/papers/TMHP` on `main` remains. The BITZER pilot and compressor-validation branches were merged, together with the missing CI interpreter guard. The auxiliary/fan/ground-loop WIP already superseded by main was recorded as integrated. Branches corresponding to removed worktrees were deleted locally; unrelated manuscript/archive branches were retained. No remote push was performed.

Complete worktree snapshots (excluding reproducible `.venv` environments), original dirty patches, original heads and branch names are preserved under `/home/habin/papers/temp/tmhp-consolidation-20261006/`; see `worktrees.json`. Uncommitted BITZER research material was also copied into main's ignored local validation tree before cleanup.

Production compressor defaults use the current Notion study's independent BITZER scroll polynomials in PR and physical N [rev/s]. They preserve raw coefficients and extrapolation, without BASE/REL normalization or clipping. Unsupported efficiencies produce an infeasible operating state; an unsupported rating produces the existing reference-state error. Custom function-body errors are not swallowed by the state rejection wrapper. Historical compressor fits and analyses remain explicitly tied to the legacy module.

## Validation

- Ruff lint and formatting: passed for `src/tmhp`, `tests`, and `scripts`.
- Mypy: passed for all 52 source files.
- Compressor/BITZER, callable override, historical fit and CI focused suites: **88 passed, 1 skipped**.
- Final raw-fit and speed-solver checks, including scalar/custom compatibility: **16 passed, 1 deselected**.
- Reference-state checks plus BITZER/callable checks: **55 passed, 5 failed**. The remaining rating failures include W10/W55 water-source boiler conditions unsupported by the raw fit.
- Full regression run: **447 passed, 41 failed, 9 errors, 4 skipped**. This run preceded the final reference-error translation and the scalar solver's exact-upper-bound/nonconvergence compatibility fixes; those fixes were checked with the focused suites above. A fully passing full-suite result is **not claimed**.

The requested raw polynomial has narrower physical applicability than the prior baseline. High-lift boiler, capacity-envelope and some ground-loop scenarios remain failing, together with historical default-dependent expectations. The coefficients and physical efficiency bounds were not tuned to make those checks pass. Historical parity/seasonal plots are not verification of BITZER performance. Detailed logs are preserved in the backup directory.

## Full-run failures

```text
FAILED tests/test_capacity_envelope.py::test_widening_the_approach_bound_does_not_move_in_range_operation
FAILED tests/test_fan_part_load_bounds.py::test_default_model_interfaces_limit_generic_reference_range[AirSourceHeatPumpBoiler-dV_fan_a]
FAILED tests/test_aux_pressure_drop.py::test_shared_api_scaling_and_reference_independent_of_max[GroundSourceHeatPumpBoiler]
FAILED tests/test_fan_part_load_bounds.py::test_generic_low_load_infeasibility_is_not_zero_power_operation
FAILED tests/test_fan_single_zone_defaults.py::test_explicit_model_flow_limits_match_hx_and_power_backport
FAILED tests/test_docs_examples.py::test_coolprop_refrigerant_list_example_is_copy_runnable
FAILED tests/test_gshp_pressure_floor_optimum.py::test_cooling_search_resolves_pressure_floor_with_ground_hx_duty_equality
FAILED tests/test_gshp_pressure_floor_optimum.py::test_projected_cycle_subcooling_is_independent_of_preprojection_trial
FAILED tests/test_models_smoke.py::test_gshpb_analyze_steady - assert 0.0 > 0
FAILED tests/test_models_smoke.py::test_wshpb_analyze_steady - tmhp.reference...
FAILED tests/test_ground_loop.py::test_model_superposition_uses_field_length[4000-1-GroundSourceHeatPumpBoiler]
FAILED tests/test_ground_loop.py::test_model_superposition_uses_field_length[4000-4-GroundSourceHeatPumpBoiler]
FAILED tests/test_ground_loop.py::test_model_superposition_uses_field_length[-4000-1-GroundSourceHeatPumpBoiler]
FAILED tests/test_ground_loop.py::test_model_superposition_uses_field_length[-4000-4-GroundSourceHeatPumpBoiler]
FAILED tests/test_models_smoke.py::test_wshpb_custom_pr_and_rps - tmhp.refere...
FAILED tests/test_models_smoke.py::test_wshpb_default_pr_and_rps - tmhp.refer...
FAILED tests/test_models_smoke.py::test_non_air_source_boiler_common_eta_defaults
FAILED tests/test_reference_flow_limits.py::test_reference_and_components_remain_fixed_when_max_expands[GroundSourceHeatPump]
FAILED tests/test_reference_flow_limits.py::test_reference_and_components_remain_fixed_when_max_expands[GroundSourceHeatPumpBoiler]
FAILED tests/test_reference_flow_limits.py::test_identical_actual_point_has_identical_physics_for_both_controls[GroundSourceHeatPump]
FAILED tests/test_reference_flow_limits.py::test_identical_actual_point_has_identical_physics_for_both_controls[GroundSourceHeatPumpBoiler]
FAILED tests/test_reference_flow_limits.py::test_constant_setpoint_is_not_the_reference_or_maximum
FAILED tests/test_reference_flow_limits.py::test_each_air_side_model_exposes_independent_reference_and_max[AirSourceHeatPumpBoiler-]
FAILED tests/test_reference_state.py::test_rating_condition_is_the_family_standard_not_the_simulation_point[WaterSourceHeatPumpBoiler]
FAILED tests/test_models_smoke.py::test_dt_hx_min_default_and_custom - tmhp.r...
FAILED tests/test_reference_state.py::test_reference_state_closes_cycle_and_both_heat_exchangers[GroundSourceHeatPumpBoiler]
FAILED tests/test_reference_state.py::test_reference_state_closes_cycle_and_both_heat_exchangers[WaterSourceHeatPumpBoiler]
FAILED tests/test_models_smoke.py::test_dt_cycle_min_removed[WaterSourceHeatPumpBoiler-kwargs4]
FAILED tests/test_rps_capacity_clamp.py::test_request_above_ceiling_clamps_to_rps_max
FAILED tests/test_rps_capacity_clamp.py::test_solver_breakdown_inside_a_valid_bracket_reports_non_convergence
FAILED tests/test_reference_state.py::test_capacity_only_construction_solves_reference_state[WaterSourceHeatPumpBoiler]
FAILED tests/test_stc_tank_pump_control.py::test_gshpb_stc_tank_uses_same_pump_threshold_for_circulation
FAILED tests/test_step_kernel.py::test_analyze_dynamic_matches_golden[diurnal_2draw-300]
FAILED tests/test_step_kernel.py::test_analyze_dynamic_matches_golden[diurnal_2draw-600]
FAILED tests/test_reference_state.py::test_explicit_inputs_override_solved_values[WaterSourceHeatPumpBoiler]
FAILED tests/test_step_kernel.py::test_analyze_dynamic_matches_golden[cold_heavydraw-600]
FAILED tests/test_step_kernel.py::test_step_public_api_matches_golden[diurnal_2draw-300]
FAILED tests/test_step_kernel.py::test_step_public_api_matches_golden[diurnal_2draw-600]
FAILED tests/test_step_kernel.py::test_step_public_api_matches_golden[cold_heavydraw-300]
FAILED tests/test_step_kernel.py::test_analyze_dynamic_matches_golden[cold_heavydraw-300]
FAILED tests/test_step_kernel.py::test_step_public_api_matches_golden[cold_heavydraw-600]
ERROR tests/test_capacity_envelope.py::test_mode_argument_is_validated - tmhp...
ERROR tests/test_capacity_envelope.py::test_default_approach_bounds_are_unchanged
ERROR tests/test_capacity_envelope.py::test_ceiling_is_found_and_exceeds_nameplate_at_mild_conditions
ERROR tests/test_capacity_envelope.py::test_request_just_below_the_ceiling_is_met
ERROR tests/test_capacity_envelope.py::test_request_beyond_the_ceiling_is_capacity_limited_not_a_failure
ERROR tests/test_capacity_envelope.py::test_capacity_limited_point_delivers_the_ceiling_not_a_cheaper_part_load
ERROR tests/test_capacity_envelope.py::test_overshoot_size_does_not_change_what_is_delivered
ERROR tests/test_capacity_envelope.py::test_heating_ceiling_rises_with_outdoor_temperature
ERROR tests/test_capacity_envelope.py::test_cooling_ceiling_falls_with_outdoor_temperature
```
