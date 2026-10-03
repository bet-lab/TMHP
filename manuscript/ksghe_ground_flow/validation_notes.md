# Pump / fan model and flow validation

## Conditions and pressure selection

Reference and constant water command are 24 L/min = 0.0004 m³/s, with two parallel bores each carrying 12 L/min when active. Variable control limits are 9.6–36 L/min. The main constant strategy runs without a prescribed-flow override. Constructor, shared configuration and controller reject out-of-range constant setpoints. Lower/upper bounds are not reference definitions.

Pump: dp_total = dp_BHE(actual branch flow, 2H) + dp_aux_ref*(actual total flow/reference total flow)^n; P_pump = dp_total*V/eta. No static borehole head or tuned friction factor is added. Default auxiliary loss is zero, exponent 2. Deprecated dp_common becomes a warned reference-loss alias; direct low-level nonzero-loss calls require an explicit reference flow.

[ASHRAE source archive](references/gs_hp_pump_pressure_drop/sources.md) verifies hydronic flow² resistance and GSHP common/HX/piping losses. The 2023 Applications SI Ch35 Table8 pressure boundary of 138 kPa at .05 L/s/kW anchors a scenario, not measured hardware or a mandatory design value. At 8 kW the flow is 24 L/min. BHE-only reference loss is 16.973153 kPa; auxiliary reference loss is 121.026847 kPa. The resulting constant pump input is 92 W (eta=.60), compared with the previous BHE-only 11.315435 W at the same water flow.

## Fan validity and failed operating points

The unchanged ASHRAE Appendix G Method2 equation is P*=.0013+.147x+.9506x²−.0998x³, x=actual/reference airflow. Generic validity/control is 0.15–1.0, with actual airflow clamp and min/max flags. Reference/min/max are 1.6/.24/1.6 m³/s, reference pressure 60 Pa, efficiency .60, reference electrical power 160 W. At the lower airflow limit the unchanged equation predicts 7.104268 W; this is a curve prediction, not a measured electrical floor. No arbitrary power floor was added.

[Fan source archive](references/fan_part_load/sources.md) contains the original equation, Handbook minimum-ratio caution (.15 example), and Addendum u turndown. The informative Addendum foreword discusses 15% airflow / 16% power for multizone VAV; the amended body deletes the old 30% sentence without inserting 16%. Its airflow provision includes minimum outdoor air. This is not a normative 16% requirement or a universal heat-pump fan law. A custom curve can declare limits beyond reference; UA normalization/scaling remains independent.

PLR .3 has no feasible steady operating point under the unchanged PR≥1.5 constraint, ground/indoor UA1600/800, water limits and new fan minimum. Per user decision, conditions are retained and cycling is not introduced. Requested failures are kept in CSV with converged=False, hp_is_on=False, explicit failure_reason, zero delivered power/flow and NaN COP. Failed-state indoor temperatures/airflow describe a rejected candidate, not running equipment. They are excluded from plotted performance and paired savings. This prevents a failed 0 W result from appearing as a low-power optimum.

## Requested operating points

| PLR | Strategy | Status | Water L/min | Pump W | Fan W | Air m³/s | Reason |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 0.3 | constant | infeasible | — | — | — | — | load_hx_capacity_insufficient |
| 0.3 | optimal | infeasible | — | — | — | — | load_hx_capacity_insufficient |
| 0.4 | constant | feasible | 24.00000 | 92.00000 | 8.87346 | 0.279513 | none |
| 0.4 | optimal | feasible | 9.60000 | 6.11180 | 11.26388 | 0.327619 | none |
| 0.5 | constant | feasible | 24.00000 | 92.00000 | 15.29220 | 0.399133 | none |
| 0.5 | optimal | feasible | 10.69802 | 8.41435 | 20.23388 | 0.475820 | none |
| 0.6 | constant | feasible | 24.00000 | 92.00000 | 25.24163 | 0.545082 | none |
| 0.6 | optimal | feasible | 12.33526 | 12.81599 | 33.36442 | 0.645045 | none |
| 0.7 | constant | feasible | 24.00000 | 92.00000 | 40.43002 | 0.723113 | none |
| 0.7 | optimal | feasible | 14.08888 | 18.98746 | 52.51436 | 0.843266 | none |
| 0.8 | constant | feasible | 24.00000 | 92.00000 | 63.44026 | 0.941338 | none |
| 0.8 | optimal | feasible | 15.92815 | 27.30029 | 79.93090 | 1.075813 | none |
| 0.9 | constant | feasible | 24.00000 | 92.00000 | 98.17772 | 1.210730 | none |
| 0.9 | optimal | feasible | 17.95128 | 38.89978 | 118.06672 | 1.345613 | none |
| 1.0 | constant | feasible | 24.00000 | 92.00000 | 150.64046 | 1.546638 | none |
| 1.0 | optimal | feasible | 21.81124 | 69.28254 | 159.85600 | 1.600000 | none |

## Paired feasible comparison

| PLR | Optimal water L/min | BHE / aux / total kPa | Total saving % | COP gain % | Water bound |
| --- | ---: | --- | ---: | ---: | --- |
| 0.4 | 9.60000 | 3.55495 / 19.36430 / 22.91924 | 20.03897 | 25.06092 | min |
| 0.5 | 10.69802 | 4.26788 / 24.04730 / 28.31518 | 15.14150 | 17.84323 | interior |
| 0.6 | 12.33526 | 5.43207 / 31.97095 / 37.40302 | 11.13245 | 12.52701 | interior |
| 0.7 | 14.08888 | 6.80953 / 41.70733 / 48.51686 | 8.06138 | 8.76822 | interior |
| 0.8 | 15.92815 | 8.39508 / 53.30767 / 61.70275 | 5.43102 | 5.74292 | interior |
| 0.9 | 17.95128 | 10.30119 / 67.70955 / 78.01073 | 3.15171 | 3.25427 | interior |
| 1.0 | 21.81124 | 14.39400 / 99.95856 / 114.35256 | 1.04339 | 1.05440 | interior |

## Auxiliary-loss sensitivity

Every multiplier has its own recomputed full-load refrigerant reference; all cases use the new 15% fan lower bound. Paired savings use only common feasible PLRs.

| Multiplier | Aux ref kPa | Paired PLRs | Optimal water range L/min | Max saving % | Max COP gain % |
| --- | ---: | --- | --- | ---: | ---: |
| 0 | 0.00000 | [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0] | 14.32217–31.85757 | 2.73839 | 2.81549 |
| 0.75 | 90.77014 | [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0] | 9.69231–21.83219 | 16.22618 | 19.36903 |
| 1 | 121.02685 | [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0] | 9.60000–21.81124 | 20.03897 | 25.06092 |
| 1.25 | 151.28356 | [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0] | 9.60000–20.80294 | 23.49413 | 30.70893 |

Base feasible optimal flow: 9.60000–21.81124 L/min. Lower-bound PLRs: [0.4]; upper-bound PLRs: []. The pump head scenario changes the hydraulic/thermal tradeoff; these are scenario results, not measured savings.

## Closure, optimization and provenance

- Main sweep: 184 requested, 161 feasible. Selected: 16 requested, 14 feasible. Paired PLRs: [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]. Failures: [{'kind': 'constant', 'plr': 0.3, 'failure_reason': 'load_hx_capacity_insufficient'}, {'kind': 'optimal', 'plr': 0.3, 'failure_reason': 'load_hx_capacity_insufficient'}].
- All successful points satisfy requested cooling, both HX duties, BHE fluid-temperature closure, pressure decomposition/scaling, water/fan limits, compressor speed/PR bounds, efficiency and mass-flow parity. Maximum ground HX residual 0.06970431 W; temperature residual 0.0000016350 K; existing tolerances retained.
- Total power = compressor + pump + indoor fan. COP_comp = delivered indoor heat / compressor input; COP_sys = delivered indoor heat / total input. Ground rejection includes refrigerant work and pump heat once; motor/drive losses are external to refrigerant duty.
- Shared compressor baseline v2026-09-24; rated refrigerant flow 0.0411887175 kg/s. Physical ground/indoor UA1600/800 W/K, variable UA/Rb* and epsilon–NTU retained. Main optimizer minus independent-grid gaps: [-0.005384996480302107, -0.10582181434949689] W (≤.5 W). Infeasible PLRs require the independent scan to contain no feasible point.
- Fresh extra computations: 237 auxiliary-loss points, 182 flow-maximum sensitivity points, 88 undersized-HX stress points, and 16 matched scalar-efficiency points. Failure masks are preserved throughout comparisons.
- Fingerprints record original base commits and actual executed working-tree module/config/script hashes. Historical paper UA800/1600 differs from the active study UA1600/800; paper before/after includes that stale-placement correction. New auxiliary sensitivity at shared fan/UA conditions isolates pressure-loss effects. Historical pump-only and original active results are archived separately.
- Final validation: 344 passed, 3 skipped. Native one-page HWPX/PDF and new 1×4 figure are checked against fresh CSVs; Linux font-substituted PDF rendering is used.
- Original ASHRAE passages were verified and highlighted; Chrome Bridge is unavailable, so the specified Bridge capture remains unfulfilled. Chromium/Poppler fallback is explicitly recorded. Remaining calibration needs: installed HX/common pressure curve, fan hardware map, pump efficiency map and UA fractions.

## Fan source follow-up — 2026-10-03

Four original Windows Chrome PDF/HTML highlights, including the normative body, are in [the updated archive](references/fan_part_load/windows_chrome_20261003/sources.md). Direct CDP access used the user-opened Chrome; no Chrome Bridge MCP was registered. Fan source documentation changed after numerical execution; the executable AST is unchanged and original execution hashes are preserved in source_documentation_changes.json.
