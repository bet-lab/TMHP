# Capacity-based UA and compressor-loss review

- Ground UA is 0.18 × rated capacity, 1440 W/K at 8 kW; independent indoor UA is 640 W/K.
- Coefficient 0.18 K⁻¹ is an engineering sizing assumption requiring calibration. Longo supports qualitative mass-flux dependence only.
- eta_v=0.9 sets speed; electrical input is gas work/eta_em with eta_em=0.8. Motor losses are outside the refrigerant cycle.
- Electrical total and COP include compressor, pump and indoor fan. Refrigerant heat balances use gas work.
- Old 800 W/K and ideal-efficiency results are archived in the simulation repository. New differences combine UA and efficiency changes.
- All 184 main operating points are feasible. All 88 undersized-HX stress points fail with NaN COP.
- Regenerated CSV claims: flow 45.3–100%, total savings up to 4.15%, COP gain up to 4.33%; PLR 0.8–1.0 selects the upper bound.
- All active graphs use scientific style and actual Dartwork-mpl MCP review, followed by visual inspection.
- Native HWPX and one-page PDF retain authors, affiliations and acknowledgement. Linux font substitution is not Hancom Office validation.
