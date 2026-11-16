# Fan airflow archive

Largest published fan airflow [m3/s] against rated capacity, cooling and heating, for: wall-mounted and
floor-standing indoor units of 1:1 single splits and their outdoor units (ASHP); water-to-air packaged heat
pumps (`indoor_type=water_to_air`, GSHP indoor side); air-to-water outdoor units (`indoor_type=air_to_water`,
outdoor rows, ASHPB). Ducted and cassette split indoor units are out of scope (user decision 2026-10-26).

Air-to-water outdoor units (added 2026-11-14, widened and **shipped** 2026-11-15):

- Status: **shipped by owner decision (2026-11-15: 'switch to the 6-manufacturer stratum')** as a central value with
  the spread between manufacturers stated (`strata_status.json`, hand-edited). `estimate_max_airflow` returns, in
  `assumptions`, the manufacturer hold-out MAPE and bias and the range of per-manufacturer biases ('Spread between
  manufacturers ...'), and the recorded reason, with the missed criteria, as the last line ('Note: ...'). An airflow
  given for the actual unit takes precedence over the default.
- Pre-set criteria (round-6 brief, written before the fit, kept in the worker's evidence folder as `criteria.md`):
  manufacturers >= 5, families >= 8; manufacturer hold-out |bias| <= 10 % and MAPE <= 20 %; family hold-out MAPE <= 20 %;
  out-of-sample Panasonic (nine `validation/catalogs` units, never fitted) |bias| <= 10 % and MAPE <= 20 %; the
  explicit-max-only fit within 0.90-1.10 of the all-row fit. Judgement of the shipped fit (6 manufacturers, 13 families,
  59 rows; linear 0.0864 x + 0.221): met — breadth, manufacturer hold-out bias +9.7 %, Panasonic bias -8.0 % / MAPE
  14.1 %, explicit-max ratio 0.99-1.08; **missed — manufacturer hold-out MAPE 22.7 % and family hold-out MAPE 20.2 %**.
  The owner saw these criteria (then 22.5 % / 20.05 %, Panasonic -8.0 % / 14.2 %, 1.516 m3/s at 15 kW) and chose to ship
  the central value with the spread stated; re-founding LG on LG's own fiches moved them by at most 0.2 %p.
- Per-manufacturer hold-out bias (law fitted to the others, minus the maker's own rows): Mitsubishi +24.6 %, Daikin
  +20.2 %, Toshiba +7.9 %, Vaillant -4.3 %, Samsung -13.7 %, LG -27.5 %.
- `capacity_text` is the **model size**: the heating capacity the manufacturer declares as the unit's size (user decision
  2026-11-14), read exactly as printed. Mitsubishi defines it as the heating reference capacity (Partload1) at A2/W35
  (Ecodan R290/R32 databook Vol.6.0) or at A-7/W35 (PUZ-WZ databook Vol.1.0); Samsung prints the kW class; Daikin
  'Heating capacity Nom.' at A7/W35; Toshiba 'Heating capacity' at A7/W35; LG nominal capacity at A7/W35; Vaillant
  prints no size row, so the printed A-7/W35 heat output (the value matching the marketed 3.5/5/7/10/12 kW class) is
  used, by analogy with PUZ-WZ. The printed A7/W35 EN 14511 rating is often a reduced or part-load point (e.g. PUZ-WZ100
  5.80 kW against a 10.0 kW reference capacity) and is kept in `note`. The model number is not decoded.
- NIBE S2125 prints no model-size capacity (its A7/W35 row is EN 14511 partial load); its rows are recorded and excluded.
- Airflow labels: `explicit_max` (Daikin 'Heating High', Vaillant 'Fan, air flow, maximum'), `rated` (LG ErP
  'Rated air flow rate, outdoors', Toshiba 'Standard air capacity' — any label containing rated/nominal/standard), `single_value`
  (Mitsubishi, Samsung). Outdoor rated and single values are taken as the maximum on measured grounds: where a document
  prints both, the maximum equals the ErP 'rated air flow rate, outdoors' (Daikin, four models) or exceeds it by 0-7 %
  (NIBE S2125-8/-12/-16; 31 % for S2125-20).
- Third-party hosts: an unmodified manufacturer PDF found only on a third-party host (`evidence_type=*_third_party_host`)
  is recorded but fitted only once its source has `"owner_accepted": true` with an `owner_acceptance` note naming the
  owner decision and the manufacturer-domain documents that print the same values. 2026-11-15: Daikin EEDEN22 accepted
  (product table, ErP fiches and catalogue on daikin.eu match all 4 rows); Vaillant 105/125 re-pointed to Vaillant's own
  English edition (0020330792_03), the German third-party copy kept as a cross-check.
- LG rows rest on LG's own documents (`LG_GR_ERP_THERMAV_R32_MONOBLOC`): the ErP technical documentation of each of the
  six 1-phase models on lg.com/gr prints two rated outdoor airflows, Low Temp and Mid. Temp (5.5-9 kW 2388 / 3690 m3/h;
  12-16 kW 7200 / 4800 m3/h); both are in the row note and the larger is fitted, because the stratum estimates the
  maximum. Capacity is LG's nominal capacity at 7 °C / 35 °C from LG's leaflet. The third-party-hosted product data book
  5BPM5-01D (60.0 m3/min x 1 / x 2) is kept as a cross-check only: 2.4 % below the fiche for the small units, equal for
  the large ones.
- Samsung pages come from samsung-climatesolutions.com, accepted as official (operated by Samsung Electronics Air
  Conditioner Europe B.V. / Samsung Electronics Limited per its privacy notice; linked from samsung.com).
- The 2026-11-15 originals are on the lab NAS (`nas_path`, `archive87_dataset_id`); their SHA-256 were checked there
  against `sources.json` on 2026-11-15 (19 files of 13 sources).

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
