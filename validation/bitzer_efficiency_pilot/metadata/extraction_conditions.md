# Extraction conditions

## How the numbers were obtained

The BITZER web software (React front end) sends every input change to its backend as a JSON Patch
(`PATCH /api/v1/calc?mod=<module>` with `[{"op": "replace", "path": "/<element>/<field>", "value": ...}]`)
and reads the Result tab with `GET /api/v1/calc/results?mod=<module>`. Technical data (displacement) comes from
`GET /api/v1/CalculationTabs/techData`. This was found by recording the network traffic of the web UI in Chrome
(CDP bridge to the user's browser) and then replayed with an anonymous session id from `scripts/bzapi.py`, so the
user's own browser session was not altered. A spot check reproduced the UI value exactly
(GSD80235VA, R410A, SST 0 °C, SDT 50 °C, External FI 50 Hz: 43.8 kW / 17.55 kW / 1077 kg/h / 95.8 °C).

`scripts/extract.py` per point: set SST, SDT, External-FI frequency -> read Result. Rows the software refuses
("Out of application ranges (see Limits)!") are kept in the raw CSV with `BITZER_limit_status` = the error number
and the message, and carry no performance values. Nothing outside the envelope is forced or extrapolated.

## Fixed settings

| Input | Value |
| --- | --- |
| Module | `ESC` (Scroll compressors, hermetic) |
| Mode | Refrigeration and air conditioning (`cooling`) |
| Reference temperature | Dew point (SST / SDT are dew-point saturation temperatures) |
| Compressor type | Single compressor |
| Liquid subcooling (in condenser) | 0 K |
| Suction gas superheat | 10 K |
| Useful superheat | off (100 %) |
| Capacity control | External FI, frequency set explicitly |
| Power supply | 50 Hz, standard 400 V motor |

## Grid

SST −15, −10, −5, 0, 5 °C × SDT 35, 40, 45, 50, 55 °C × f 35, 40, …, 75 Hz (9 points) = 225 requests per compressor.

## Speed variables

- `frequency_Hz` (primary, as reported by BITZER "Compressor frequency")
- `normalized_speed` = f / 50 Hz, basis `frequency_ratio`, reference `manufacturer_nominal_frequency_50Hz`
  (BITZER rates displacement at 2900 rpm / 50 Hz)
- `N_rps_assumed` = f · 2900 / 50 / 60 — shaft speed assuming the 50 Hz slip ratio holds. EST-420 gives
  GSD6..8 35..75 Hz = 2000..4400 rpm (57.1 and 58.7 rpm/Hz), consistent with 58 rpm/Hz within ±1.5 %.
  Used only for η_v; f and n* are kept as raw/derived variables independent of it.

Because every compressor in the pilot shares f_ref = 50 Hz, n* is an affine transform of f; polynomial
regressions in n* and in f are therefore the same model with rescaled coefficients (identical fit and CV).

## What BITZER's outputs mean (as far as could be established)

- **Power input**: compressor electrical input at the motor terminals (FI output side). The current is shown at
  the FI output voltage (e.g. "Current (280V)" at 35 Hz, U/f control); EST-420 treats FI losses as an extra on
  top. FI (drive) losses are therefore *not* in this power.
- **Discharge gas temp. w/o cooling**: no definition in the software or EST-420. Empirically, for every valid
  point the software's condenser capacity equals cooling capacity + power input (closure 1.000 ± 0.001), and
  ṁ(h₂ − h₁) computed from this temperature is 0.99·P_el. The temperature is thus consistent with an adiabatic
  energy balance with a ~1 % heat loss, not with an independent measurement of the discharge state.
  Consequence: the split of the electrical-to-isentropic product into η_is and η_em is *not identified* by
  these data (see README).
- "Tentative data" note is shown on every ORBIT result.
