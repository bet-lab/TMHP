# Fan airflow archive

Largest published fan airflow [m3/s] against rated capacity, cooling and heating, for: wall-mounted and
floor-standing indoor units of 1:1 single splits and their outdoor units (ASHP); water-to-air packaged heat
pumps (`indoor_type=water_to_air`, GSHP indoor side); air-to-water outdoor units (`indoor_type=air_to_water`,
outdoor rows, ASHPB). Ducted and cassette split indoor units are out of scope (user decision 2026-10-26).

Air-to-water outdoor units (added 2026-11-14, widened 2026-11-15, **provisional — awaiting owner reconfirmation**):

- Status: **provisional, awaiting owner reconfirmation** (`strata_status.json`, hand-edited). The owner approved
  (2026-11-15) shipping the *6-manufacturer* stratum as a central value with the spread between manufacturers stated,
  subject to the third-party copies matching the manufacturers' own domains. LG did not match, so the current fit is a
  5-manufacturer stratum, which the owner has not approved; it stays provisional until the owner decides on it.
  `estimate_max_airflow` already returns, in `assumptions`, the manufacturer hold-out MAPE and bias and the range of
  per-manufacturer biases ('Spread between manufacturers ...') and the reason as the last line ('Provisional: ...'). An
  airflow given for the actual unit takes precedence over the default.
- Pre-set criteria (round-6 brief, written before the fit, kept in the worker's evidence folder as `criteria.md`):
  manufacturers >= 5, families >= 8; manufacturer hold-out |bias| <= 10 % and MAPE <= 20 %; family hold-out MAPE <= 20 %;
  out-of-sample Panasonic (nine `validation/catalogs` units, never fitted) |bias| <= 10 % and MAPE <= 20 %; the
  explicit-max-only fit within 0.90-1.10 of the all-row fit. Judgement, as fitted (5 manufacturers, 12 families, 53 rows):
  manufacturer hold-out MAPE 19.6 %, bias +8.9 %; family hold-out MAPE 17.4 %; explicit-max-only ratio 0.98-1.01 — met.
  **Panasonic bias -15.1 % (MAPE 17.8 %) — missed**: 8 of the 9 Panasonic units are larger than the default. The owner
  saw the criteria table (including the 6-manufacturer variant, which missed manufacturer and family hold-out MAPE at
  22.5 % and 20.05 % but had a Panasonic bias of -8.0 %) and chose to ship that stratum with the spread stated.
  After that decision the LG rows failed the official-domain check (below), so the fit is the 5-manufacturer one,
  whose criteria outcome differs (hold-out criteria met, Panasonic missed); it awaits the owner's reconfirmation.
- Per-manufacturer hold-out bias (law fitted to the others, minus the maker's own rows): Mitsubishi +19.5 %, Daikin
  +11.1 %, Toshiba +2.5 %, Vaillant -7.1 %, Samsung -17.5 %.
- `capacity_text` is the **model size**: the heating capacity the manufacturer declares as the unit's size (user decision
  2026-11-14), read exactly as printed. Mitsubishi defines it as the heating reference capacity (Partload1) at A2/W35
  (Ecodan R290/R32 databook Vol.6.0) or at A-7/W35 (PUZ-WZ databook Vol.1.0); Samsung prints the kW class; Daikin
  'Heating capacity Nom.' at A7/W35; Toshiba 'Heating capacity' at A7/W35; LG nominal capacity at A7/W35; Vaillant
  prints no size row, so the printed A-7/W35 heat output (the value matching the marketed 3.5/5/7/10/12 kW class) is
  used, by analogy with PUZ-WZ. The printed A7/W35 EN 14511 rating is often a reduced or part-load point (e.g. PUZ-WZ100
  5.80 kW against a 10.0 kW reference capacity) and is kept in `note`. The model number is not decoded.
- NIBE S2125 prints no model-size capacity (its A7/W35 row is EN 14511 partial load); its rows are recorded and excluded.
- Airflow labels: `explicit_max` (Daikin 'Heating High', Vaillant 'Fan, air flow, maximum'), `rated` (LG 'Air Flow
  Rate Rated', Toshiba 'Standard air capacity' — any label containing rated/nominal/standard), `single_value`
  (Mitsubishi, Samsung). Outdoor rated and single values are taken as the maximum on measured grounds: where a document
  prints both, the maximum equals the ErP 'rated air flow rate, outdoors' (Daikin, four models) or exceeds it by 0-7 %
  (NIBE S2125-8/-12/-16; 31 % for S2125-20).
- Third-party hosts: an unmodified manufacturer PDF found only on a third-party host (`evidence_type=*_third_party_host`)
  is recorded but fitted only once its source has `"owner_accepted": true` with an `owner_acceptance` note naming the
  owner decision and the manufacturer-domain documents that print the same values. 2026-11-15: Daikin EEDEN22 accepted
  (product table, ErP fiches and catalogue on daikin.eu match all 4 rows); Vaillant 105/125 re-pointed to Vaillant's own
  English edition (0020330792_03), the German third-party copy kept as a cross-check; LG product data book 5BPM5-01D
  **not accepted** — capacities match LG's leaflet, but no LG-domain document prints its airflow 60.0 m3/min, and LG's
  ErP fiche for HM091M U43 prints 3690 m3/h (medium temperature) / 2388 m3/h (low temperature), not 3600 m3/h.
- Samsung pages come from samsung-climatesolutions.com, accepted as official (operated by Samsung Electronics Air
  Conditioner Europe B.V. / Samsung Electronics Limited per its privacy notice; linked from samsung.com).
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
