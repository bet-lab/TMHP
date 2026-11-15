# Fan airflow archive

Largest published fan airflow [m3/s] against rated capacity, cooling and heating, for: wall-mounted and
floor-standing indoor units of 1:1 single splits and their outdoor units (ASHP); water-to-air packaged heat
pumps (`indoor_type=water_to_air`, GSHP indoor side); air-to-water outdoor units (`indoor_type=air_to_water`,
outdoor rows, ASHPB). Ducted and cassette split indoor units are out of scope (user decision 2026-10-26).

Air-to-water outdoor units (added 2026-11-14):

- Shipped as **provisional** (`strata_status.json`, decision 2026-11-14): `estimate_max_airflow` returns the value with
  `status="provisional"` and the reason in `assumptions`. `strata_status.json` is hand-edited; a stratum not listed is
  `shipped`.
- `capacity_text` is the **model size**: the heating capacity the manufacturer declares as the unit's size (user decision
  2026-11-14), read exactly as printed. Mitsubishi defines it as the heating reference capacity (Partload1) at A2/W35
  (Ecodan R290/R32 databook Vol.6.0) or at A-7/W35 (PUZ-WZ databook Vol.1.0); Samsung prints the kW class. The printed
  A7/W35 EN 14511 rating is often a reduced or part-load point (e.g. PUZ-WZ100 5.80 kW against a 10.0 kW reference
  capacity) and is kept in `note`. The model number is not decoded (SUZ-SWM80 is 7.5 kW as printed).
- NIBE S2125 prints no model-size capacity (its A7/W35 row is EN 14511 partial load); its rows are recorded and excluded.
- Samsung pages come from samsung-climatesolutions.com, accepted as official (operated by Samsung Electronics Air
  Conditioner Europe B.V. / Samsung Electronics Limited per its privacy notice; linked from samsung.com).

Widened 2026-11-15 (round 6, still **provisional**): Daikin Altherma 3 M (EBLA-D), Vaillant aroTHERM plus, LG Therma V
R32 Monobloc and Toshiba Estia R32 added, so the stratum has 6 manufacturers, 13 families and 59 rows. Panasonic is kept
out on purpose: its nine `validation/catalogs` units are the out-of-sample check.

- Model size per maker: Daikin 'Heating capacity Nom.' at A7/W35; LG nominal capacity at A7/W35 (= its line-up class);
  Toshiba 'Heating capacity' at A7/W35; Vaillant prints no size row, so the printed A-7/W35 heat output (the value that
  matches the marketed 3.5/5/7/10/12 kW class) is used, by analogy with Mitsubishi PUZ-WZ.
- Airflow labels now differ: `explicit_max` (Daikin 'Heating High', Vaillant 'Fan, air flow, maximum'), `rated` (LG
  'Air Flow Rate Rated', Toshiba 'Standard air capacity' — any label containing rated/nominal/standard), `single_value`
  (Mitsubishi, Samsung). Outdoor rated and single values are still taken as the maximum, now on measured grounds: where a
  document prints both, the maximum equals the ErP 'rated air flow rate, outdoors' (Daikin EEDEN22, four models, ratio
  1.00) or exceeds it by 0-7 % (NIBE S2125-8/-12/-16; 31 % for S2125-20), and the explicit-max-only fit lies within
  0.99-1.08 of the all-row fit at the stratum's capacity quartiles (`regression.json` `explicit_max_only`).
- Third-party hosts: Daikin EEDEN22, LG product data book 5BPM5-01D and Vaillant 0020326635_03 are unmodified manufacturer
  PDFs that no manufacturer domain serves publicly (Daikin keeps its databooks behind its business portal). They are kept
  with `evidence_type=*_third_party_host`; the Daikin values are corroborated by Daikin's own ErP fiche
  (`DAIKIN_ERP_EBLA11DAW1`, cross-check). Dropping all three leaves 4 manufacturers and moves the Panasonic bias from
  -8.0 % to -15.8 %.
- Why still provisional (pre-set criteria, round-6 brief, written before the fit): manufacturer hold-out MAPE 22.5 %
  (limit 20 %) and family hold-out MAPE 20.05 % (limit 20 %) miss; manufacturer hold-out bias +9.6 % (limit 10 %),
  Panasonic bias -8.0 % / MAPE 14.2 % (limits 10 % / 20 %), breadth and the label test pass. At equal model size the
  makers differ systematically: a law fitted to the other makers under-predicts LG by 27 % and Samsung by 14 %, and
  over-predicts Mitsubishi by 24 % and Daikin by 20 % (Daikin's values are explicit maxima). The model-size definitions
  also differ by maker.
- Not yet on the lab NAS: the 2026-11-15 originals are kept locally (`nas_path` null with a note); transfer per #87.

Water-to-air units (added 2026-11-14):

- Capacity is the ISO 13256-1 ground-loop (GLHP) full-load rating printed in the same document.
- `airflow_text` is the largest printed blower airflow **usable with the compressor running**: the top of the
  boldface L-H range (WaterFurnace), the `Maximum`/`MAX` row (ClimateMaster), the highest program's mode 2nd stage
  (Enertech), Setting 4 High (Daikin Applied). Settings printed only for auxiliary/emergency electric heat are
  larger on some sizes; TMHP models no auxiliary heater, so they stay in `note`.
- A printed rated/nominal indoor airflow (the AHRI/ISO test airflow, e.g. Bosch 'Rated Airflow at 400 CFM/Ton')
  is kept but excluded from fits.
- Maximum external static pressure is defined differently by each maker (wet coil with or without filter); it is
  recorded in `note`, not harmonised.
- WaterFurnace, ClimateMaster and Enertech belong to the NIBE group but publish separately engineered product
  lines; Daikin Applied is independent. The shipping gate counts brands. Strata: `airflow|<mode>|indoor|<indoor_type>` and
`airflow|<mode>|outdoor|single_split`.

The archive works like [../compressor_displacement](../compressor_displacement/README.md): hand-edited
`sources.json`, `observations/<source_id>.csv` (values exactly as printed) and `errata.csv`; generated
`measurements.*`, `regression.json`, `audit.json`; append-only `releases.json`. Shared mechanics live in
`../archive.py`.

Observation columns: `record_id, source_pdf_page, printed_page, locator, manufacturer, family_id,
indoor_model, outdoor_model, refrigerant, side, indoor_type, mode, capacity_text, capacity_unit, airflow_text,
airflow_unit, fan_step, note`.

- `indoor_type` is the indoor unit type; outdoor rows carry the type of the indoor unit they are paired with.
- `airflow_text` is the largest printed fan step and `fan_step` its printed label (High, SHi, Max …).
  A row without a printed step is kept but excluded from fits: a nominal airflow is not the maximum.
- Units are converted in the build only: capacity W, kW, Btu/h; airflow m3/s, m3/min, m3/h, l/s, CFM.

```bash
uv run python -m validation.default_inputs.fan_airflow.build            # prints changes since the latest release
uv run python -m validation.default_inputs.fan_airflow.audit            # needs local evidence files
uv run python -m validation.default_inputs.fan_airflow.build --release fan-airflow-vYYYY-MM-DD --note "..."
```

Migrated 2026-10-26 from the wall-mounted rows of `../data/measurements.json` (Daikin FTXM-A, Fujitsu 2018
EU/UK catalogues). The wall-mounted indoor fits equal the 2026-10-22 fits; outdoor fits differ because outdoor
units paired with cassette indoor units are no longer pooled.
