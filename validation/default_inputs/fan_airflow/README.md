# Fan airflow archive

Largest published fan airflow [m3/s] against rated capacity, cooling and heating, for: wall-mounted and
floor-standing indoor units of 1:1 single splits and their outdoor units (ASHP); water-to-air packaged heat
pumps (`indoor_type=water_to_air`, GSHP indoor side); air-to-water outdoor units (`indoor_type=air_to_water`,
outdoor rows, ASHPB). Ducted and cassette split indoor units are out of scope (user decision 2026-10-26).

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
