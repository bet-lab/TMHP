# Reference and operating-limit revision review

- Normalization remains 24 L/min for all strategies and control ceilings; constant command is 24 and variable bounds are 9.6–36 L/min.
- Ground UA 1440 W/K, indoor UA 640 W/K, eta_v=.9 and eta_em=.8 remain unchanged. Scaling does not cap UA at its reference value.
- Actual branch flow drives Rb* and pump pressure drop. Prescribed 24 L/min fairness checks match UA, Rb*, pump, compressor state and fan across all three maxima.
- Low-load 36 L/min requires an indoor approach above the former 20 K numerical search ceiling; explicit 1–25 K is used consistently throughout the new study. Default model behavior remains unchanged.
- Full-load 36 L/min gives compressor 892.240 W, pump 34.272 W, fan 159.851 W, total 1086.364 W and COP 7.3640. Relative to constant 24 L/min, total power falls 5.4954% and COP rises 5.8150%.
- Full load remains on every tested maximum. The manuscript describes a constrained optimum and the notes review uncalibrated pump/common-loss/UA assumptions. Low-load numerical differences under 0.5 W are disclosed.
- All 184 main and 48 sensitivity comparison points are feasible; all 88 undersized-HX points are rejected with NaN COP.
- The one-page manuscript includes only the four-panel performance figure. All four study figures use scientific style and actual Dartwork-mpl MCP review plus visual inspection.
- Native HWPX text, PDF text, numerical claims, figure bytes, authors, affiliations, acknowledgement and embedded fonts pass QA. The before/after PDF and native-text diff were inspected.
- Linux font substitutes produce the supplied checked PDF; Hancom Office validation was not performed.

- Requested axis/letter format is applied in all four active figures, rechecked via MCP and visual inspection, and embedded in the rebuilt HWPX/PDF.
