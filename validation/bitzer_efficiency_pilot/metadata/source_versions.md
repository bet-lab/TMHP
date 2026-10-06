# Source versions

| Item | Value |
| --- | --- |
| Software | BITZER Software, web edition, **v7.1.11.4** (footer of https://www.bitzer.de/websoftware/) |
| Backend | `https://bitzer-virtual-api.germanywestcentral.cloudapp.azure.com/api/v1/` (Azure; the same API the web UI calls) |
| Extraction date | 2026-10-04 (KST) |
| Session | anonymous (`User/Profile` -> `Authenticated: false`), units SI, country Germany, language English |
| Result note shown on every ORBIT result | "Tentative data. The values are based on tentative data. Larger deviations may occur at the limits of the application range ..." and "*according to EN12900 (10K suction gas superheat, 0K liquid subcooling)" |
| Property backend for derivation | CoolProp (HEOS) via the TMHP `uv` environment, same `PropsSI` calls as `src/tmhp/air_source_heat_pump.py`; R454B as `HEOS::R32[0.829248]&R1234yf[0.170752]` (same mapping as `validation/compressor_maps/derive.py`) |

## Documents consulted

- BITZER EST-420-8, *Operation of BITZER scroll compressors with external frequency inverters*
  (https://www.bitzer.de/shared_media/html/est-420/en-GB/index.html), accessed 2026-10-04. Facts used:
  - "At present, the Bitzer Software offers calculations with frequency inverter only for Orbit compressors."
  - Frequency / speed ranges: GSD6..GSD8 35..75 Hz = 2000..4400 min^-1; GSU6..GSU8 35..75 Hz = 2100..4500 min^-1;
    GSP6 45..75 Hz; GSP8 50/60 Hz only; ORBIT GED8 35..60 Hz. Minimum frequency is set by bearing lubrication.
  - 2-pole asynchronous motors: 2900 min^-1 at 50 Hz, 3500 min^-1 at 60 Hz; FI control logic U/f proportional.
  - "Electrical power consumption at full load is slightly higher than when operating the compressor directly on
    the mains supply. This is due to losses in the frequency inverter" -> FI losses are a separate item, i.e.
    not part of a compressor rating.
