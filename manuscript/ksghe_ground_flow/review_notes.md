# Compact revision review — 2026-10-01

- Scope: user-supplied compact revision plan; current 261001 document and cooling
  study. The older 251021 manuscript is not overwritten by this revision.
- Title and abstract: recommended Korean/English titles; three short paragraphs
  introducing reduced physics, model/objective, and full cooling conditions.
- Methods: actual default UA (800/640 W/K), reference refrigerant flow calculated
  from the full-load baseline, total-power objective including fan, and consistent
  user-supplied room temperature. Ground total heat is normalized by two × 100 m.
- Figure: replace two prior figures with one new 1×4 scientific-style figure.
  Actual Dartwork-mpl MCP checks cover all active plots; runtime layout checks
  have no findings. HWPX picture crop bounds, table margins, caption break and
  English title line spacing were corrected without shrinking text fonts.
- Results: two bullets from the new 16-point comparison only. Report component
  tradeoffs, 0–3.46% total savings, 0–3.58% COP gain and the PLR≥0.7 upper bound.
- Acknowledgement: retain only RS-2025-00512551; authors, affiliations and footer
  are present in both native XML and extracted PDF text.
- Evidence: QA checks all body/caption strings against rendered PDF and all
  quantitative claims against CSV. Strict and compatibility HWPX checks pass.
  Visual review found no clipped labels, captions, affiliations or overlap.
- Comparison: diff/compact_revision_20261001.pdf (visual before/after) and .diff
  (native text comparison). Verified PDF is one page with embedded fonts.
- Limits: steady fixed-wall, uncalibrated example with no common pipe loss;
  Linux font-substituted PDF rendering, not Hancom Office testing.
