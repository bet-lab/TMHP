# Fan airflow archive

Largest published fan airflow [m3/s] against rated capacity for 1:1 single splits: wall-mounted and
floor-standing indoor units and their outdoor units, cooling and heating. Ducted and cassette units are out
of scope (user decision 2026-10-26). Strata: `airflow|<mode>|indoor|<indoor_type>` and
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
