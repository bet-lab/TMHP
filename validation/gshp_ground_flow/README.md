# Ground-loop flow study (v7, issue #49)

This is an **illustrative numerical study**, not a fit to a manufacturer's
catalogue or experimental/field validation. It checks the new coupled physics
and demonstrates why compressor COP alone does not describe system performance.

## Reproduce

From the repository root, using the project environment:

```bash
uv run python validation/gshp_ground_flow/run_study.py --workers 4
uv run python validation/gshp_ground_flow/stress_hx.py
uv run python figures/mpl/gshp_ground_flow/plot_ground_flow.py
```

`--resume` reuses recorded points only with a matching configuration hash.
Do not reuse points after changing model source; rerun from scratch instead.
`results/verification.json` records source commit, input hash, package versions,
conservation residuals and the independent flow-grid comparison. Plotting reads
the CSVs, never hard-coded performance values. The graph environment must have
the project's `dartwork-mpl` dependency. Set `MPLCONFIGDIR` to a writable directory
on restricted hosts.

## Operating conditions and boundaries

All explicit inputs are in `config.json`. The paper case is heating, R410A,
8 kW reference capacity (PLR denominator), displacement 12 cm³, constant
isentropic efficiency 0.8, superheat/subcooling 5 K, compressor envelope
15–150 rev/s and pressure ratio 1.5–8. The low-lift pressure-ratio floor is active
at some loads; the large COP values depend on this idealized cycle and are not
catalogue-rated COPs.

The borefield has 2×2 identical, equally supplied U-tubes at 6 m spacing,
100 m depth and 0.08 m bore radius. Pipe inner/outer radii are 0.013/0.016 m;
half-shank spacing is 0.025 m. Ground/grout/pipe conductivities are
2/1.5/0.4 W/(m K). Water properties are fixed at rho=1000 kg/m³,
mu=0.001 Pa s, cp=4186 J/(kg K), k=0.606 W/(m K).

Both the initial ground and the prescribed steady borehole wall are 16 °C;
room air is 20 °C. The exergy reference environment is 7 °C, but this paper does
not report exergy. **These are steady points with a fixed wall, not an annual
simulation or a claim that g-function drawdown remains zero.** Dynamic ground
history is separately covered by physical/regression tests.

Rated field flow is 80 L/min (20 L/min per borehole); allowed flow is 0.2–1.2
times rated. Pump efficiency is 0.6, pipe roughness 1 micrometre, and common
header/valve/HX pressure drop is zero. Electrical pump input is included as heat
to the loop. Each branch uses 2H, not the sum of all borehole pipe lengths.
This simplified hydraulic boundary excludes headers, balancing and pump curves.

Condenser and ground-evaporator rated UA are 2000 W/K. Ground UA scales with
water and refrigerant flow, using rated refrigerant flow 0.04 kg/s, resistance
fractions 0.5/0.3/0.2 and both flow exponents 0.8. These are assumed reduced-model
parameters, not measured resistance partitions. Rb* uses the existing axial
correction with a precomputed multipole-based local resistance; it is not a
validation of that approximate axial model.

The indoor fan retains the same model in both strategies: rated air flow
1.6 m³/s, pressure 60 Pa, efficiency 0.6 (rated power 160 W), area 0.8 m² and
default part-load coefficients. At each candidate ground flow, the indoor
approach is optimized using compressor+pump+fan power. The **outer** ground-flow
objective requested by the plan is compressor+pump power; it does not claim to
globally minimize system power including the fan.

## Comparison and results

`constant` and `optimal` use **identical enabled variable-UA, variable-Rb and
hydraulic models**; only the selected ground flow differs. This isolates control
from model changes. The default legacy solver and the multi-borehole correction
are checked separately, not presented as a like-for-like control comparison.

At eight PLRs from 0.3 to 1.0, optimal flow is 0.200–0.697 of rated. Compressor+
pump input decreases by 3.86–36.78%; system COP increases by 3.43–47.74%.
At PLR 0.3 the lower flow bound is selected, so an interior optimum is not
claimed. At PLR ≥0.6 compressor COP decreases while system COP increases.
The calculated improvement is conditional on the parameters and boundaries
above and must not be interpreted as measured energy savings.

There are 184 recorded main-case operating points, all feasible. The maximum
ground-HX duty residual is 3.54e-5 W and maximum source-temperature closure
residual is 9.49e-6 K. At PLRs 0.3/0.6/1.0 the optimized objective is respectively
0.000/0.111/0.0026 W lower than the minimum of a separate 21-point flow grid.
This checks these cases, not global optimality for arbitrary/disconnected domains.

The separate stress map sets **ground rated UA to 400 W/K** and otherwise
retains the case. `hx_stress.csv` includes successful and rejected points;
rejected points have NaN COP and explicit `ground_hx_capacity_insufficient`.
It is a capacity-boundary check, not part of the paper's COP comparison.

## Outputs and physical checks

- `normalization.csv`: q'=Q/(Nb H), with fixed total Q=8000 W, H=100 m.
- `components.csv`: pump and Rb* vs flow at depths 50/100/200 m. With zero common
  loss, normalized pump curves coincide, although absolute power scales with H.
- `ua_map.csv`: two-stream UA/rated UA at water/refrigerant flow ratios 0.2–1.2.
- `operating_points.csv`: complete diagnostics for constant, optimal and scanned flows.
- `comparison.csv`: the paired paper-case quantities and percentage changes.
- `hx_stress.csv`: 88 independent undersized-HX points, including failures.
- `figures/mpl/gshp_ground_flow/output/`: figures 01–05 requested by the plan,
  objective/optimal-flow/feasibility checks 06–09, plus the two paper figures,
  all as vector PDF and 600 dpi PNG.

Physical tests in `test_ground_loop.py`, `test_ground_flow_physics.py`,
`test_ground_flow_control.py` and `test_heat_exchanger.py` check independent
normalization and energy balances, laminar pressure-drop theory, parallel
pressure vs total pumping power, rated UA, interpolation against direct Rb*,
analytic optimum, failed-cycle handling and non-mutating dynamic previews.
Existing ground/solar tests independently reconstruct ground convolution.

Sources for definitions: [pygfunction pipe resistance API](https://pygfunction.readthedocs.io/en/stable/modules/pipes.html).
The distinction between catalogue equation fitting and component parameter
models is described by the [EnergyPlus Engineering Reference](https://energyplus.readthedocs.io/en/latest/guides/engineering-reference/16.6-plant-loop-heat-pumps.html).
Neither source is experimental validation of this implementation.
