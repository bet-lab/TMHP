# Heat-exchanger approach archive

Refrigerant saturation temperatures of real machines at declared operating points, and the default
heat-exchanger conductances (UA) TMHP derives from them (issues #115–#118). Unlike the two sibling archives
the response is not a regression on capacity: what transfers from one machine to another of a different size
is an approach temperature, and the archive's hold-out test decides which one.

The archive works like [../fan_airflow](../fan_airflow/README.md): hand-edited `sources.json`,
`observations/<source_id>.csv` (values exactly as printed), `errata.csv` and `holdout_groups.json`; generated
`measurements.csv`, `regression.json`, `audit.json`; append-only `releases.json`; `unverifiable.json` lists the
transcribed values the audit cannot find in the text layer. Shared mechanics live in `../archive.py`.

## What one row is

One observation row is one refrigerant heat exchanger of one machine at one declared operating point: the
fluid entering it (temperature, and wet bulb where printed), its flow, the heat duty, and the refrigerant
pressure (or a printed saturation temperature) on that side. Observation columns are listed in
`build.OBSERVATION_COLUMNS`; `product_class`, `exchanger`, `hx_type` and `role` place the row in a stratum,
`value_kind` and `surface_state` decide whether it may enter a fit.

From those values the build computes, with no free parameter, for the model's exchanger (isothermal
refrigerant, single-phase fluid, `eps = 1 − exp(−UA/C)`):

```
ITD   = |T_fluid_in − T_sat|      (inlet temperature difference)
dT    = Q / C                     (fluid temperature change: declared duty over heat capacity rate)
eps   = dT / ITD
leave = ITD − dT                  (leaving-end approach)
UA    = −C ln(1 − eps)
```

**Effective conductance.** `T_sat` is the saturation temperature at the pressure the document prints — at the
service port or a compressor port, rarely at the coil itself. The UA that reproduces it is therefore not the
geometric `U·A` of the coil: refrigerant pressure drop in the lines and the coil, and the desuperheating and
subcooling zones, are inside it. That is the quantity the model needs, because its cycle has no pressure drop
and no single-phase refrigerant zones; the compressor sees the same pressure ratio as the real machine only if
the model's saturation temperature is the one at the compressor's ports.

The duty of the exchanger is the printed coil duty where there is one; otherwise the unit capacity at the
point, on the load side of the mode, plus the total input for the condenser or minus it for the evaporator.
Where a table prints the fluid temperature change but no duty (liquid exchangers), duty is flow × change.
Rows a model cannot use are kept and excluded with a reason (`exclusion_reason` in `measurements.csv`):

| Exclusion (`exclusion_reason`) | Rows |
|---|---|
| wet indoor coil in cooling (`surface_wet`) | 248 |
| wet, and an effectiveness outside (0, 1) | 39 |
| no fluid temperature change (no flow or no duty at the point) | 154 |
| no fluid temperature change, and a pipe-surface temperature, a wet or a frosting coil | 158 |
| effectiveness outside (0, 1) | 20 |
| superseded revision of the same manual (3 of them also wet) | 12 |
| saturation on the wrong side of the inlet (6 of them from a pipe-surface temperature) | 7 |
| no saturation temperature or no inlet temperature | 6 |
| design-selection printout (recorded, never fitted) | 2 |
| manufacturer document on a third-party host, not yet accepted, and no other reason (`third_party_copy_not_accepted`) | 65 |

A row is counted under its reasons other than `third_party_copy_not_accepted`. 243 rows carry that reason
(third-party copies, see below); for 65 of them it is the only one (last line).

Of 2,287 rows from 59 sources, 1,576 enter a fit. Rows that give only the inlet difference (a pipe-surface
temperature, no duty) are reported per stratum in `regression.json` → `itd_only_rows` as corroboration.

## Strata

A stratum is the exchanger kind a default is issued for and the refrigerant's role (condensing, evaporating):

- `outdoor_coil|fin_tube|<role>` — outdoor fin-and-tube coils with a propeller fan, **pooled across product
  classes**;
- `indoor_coil|<indoor kind>|fin_tube|<role>` — indoor coils, one stratum per kind of indoor unit
  (wall-mounted, ducted, other split indoor units, water-to-air);
- `liquid_source_hx|<product class>|<hx type>|<role>`, `load_water_hx|…` — liquid-to-refrigerant exchangers;
- `water_condenser|bphe|<role>` — the brazed-plate condenser of air-to-water units.

Outdoor coils are one construction whichever unit they serve, and the data agree across classes. Indoor coils
are not: the leaving approach of the indoor unit of a wall-mounted mini-split and of a ducted air handler differ
by a factor of two or more, so pooling them would put every one of them far from the default.

Leaving-approach median [K] (inlet-difference median [K]) by product class, hold-out groups weighted equally
(`regression.json` → `fits.<stratum>.by_product_class`):

| Outdoor coil | Wall-mounted split | Ducted split | Other split indoor | Air-to-water |
|---|---|---|---|---|
| condensing | 2.54 (9.2), 97 rows / 6 groups | 2.27 (8.4), 10 / 3 | 3.94 (10.1), 4 / 1 | 3.74 (12.0), 9 / 2 |
| evaporating | 2.58 (6.5), 77 / 6 | 2.31 (4.3), 2 / 1 | — | 3.47 (7.7), 8 / 1 |

| Indoor coil, condensing | Leaving approach median [K] | Inlet difference median [K] | Manufacturers |
|---|---|---|---|
| wall-mounted | 6.85 | 21.7 | 1 |
| ducted | 2.58 | 18.4 | 5 |
| other split indoor units (floor, cassette) | 6.95 | 27.3 | 1 |
| water-to-air | 2.79 | 15.9 | 2 |

## Selection rule and outcome

The rule was fixed before the archive was built (owner decision 2026-11-16, replacing the fixed-ITD decision of
#115): per stratum, every candidate law is fitted with one **hold-out group** (product lines sharing hardware,
see below) left out, product lines weighted equally, and scored by the RMSE of the predicted inlet temperature
difference — that is, of the saturation temperature — of the held-out units at their own flow and duty. The
first law in the order *leaving approach, UA ∝ exchanger duty, UA ∝ nameplate, fixed ITD, UA linear in
nameplate* whose RMSE is within 1.10 of the lowest is selected: one parameter and always solvable first, the
fixed inlet difference (which has no solution when `Q > C·ITD`) after them, the two-parameter law last. A
stratum ships when it has at least 3 hold-out groups and 2 manufacturers (`SHIPPING_GATE`, as in #86).

| Stratum | Rows / hold-out groups / manufacturers | Leaving approach [K] median (p10–p90) | Inlet difference [K] median (p10–p90) | Hold-out RMSE [K]: leaving approach / UA ∝ duty / UA ∝ nameplate / fixed ITD / linear | Selected | Meets gate |
|---|---|---|---|---|---|---|
| `outdoor_coil\|fin_tube\|condensing` | 120 / 11 / 4 | 2.73 (0.9–3.9) | 9.4 (6.7–12.7) | 1.63 / 1.96 / — / 3.13 / — | leaving approach 2.73 K | yes |
| `outdoor_coil\|fin_tube\|evaporating` | 87 / 8 / 2 | 2.82 (2.0–4.5) | 6.8 (4.8–9.0) | 1.37 / 1.49 / 1.55 / 1.84 / 1.52 | leaving approach 2.82 K | yes |
| `water_condenser\|bphe\|condensing` | 20 / 3 / 2 | 2.01 (0.7–3.3) | 7.0 (5.8–10.3) | 1.17 / 1.53 / 2.27 / 2.22 / — | leaving approach 2.01 K | yes |
| `liquid_source_hx\|water_to_air\|coaxial\|evaporating` | 118 / 5 / 2 | 3.47 (1.0–6.6) | 7.7 (4.8–10.2) | 1.58 / 1.61 / — / 1.99 / — | leaving approach 3.47 K | yes |
| `liquid_source_hx\|water_to_air\|coaxial\|condensing` | 117 / 5 / 2 | 4.06 (1.6–6.5) | 10.2 (7.0–15.4) | 2.87 / 3.14 / — / 4.73 / — | leaving approach 4.06 K | yes |
| `indoor_coil\|water_to_air\|fin_tube\|condensing` | 113 / 5 / 2 | 2.79 (1.5–6.6) | 15.9 (13.8–20.4) | 2.72 / 2.83 / — / 3.73 / — | leaving approach 2.79 K | yes |
| `indoor_coil\|ducted\|fin_tube\|condensing` | 32 / 5 / 5 | 2.58 (0.3–8.9) | 18.4 (7.2–25.3) | 5.46 / 5.93 / — / 8.11 / — | leaving approach 2.58 K | yes (no model uses it) |
| `indoor_coil\|ducted\|fin_tube\|evaporating` | 8 / 4 / 4 | 4.06 (1.0–6.2) | 17.0 (13.6–18.0) | 1.53 / 1.34 / 1.81 / 1.55 / — | UA ∝ duty, 0.1096 /K | yes (no model uses it) |
| `indoor_coil\|wall_mounted\|fin_tube\|condensing` | 78 / 6 / 1 | 6.85 (3.8–10.1) | 21.7 (14.0–30.1) | 2.62 / 3.41 / 3.41 / 5.94 / 2.47 | leaving approach 6.85 K | **no** — one manufacturer |
| `indoor_coil\|other_indoor_unit\|fin_tube\|condensing` | 15 / 2 / 1 | 6.95 (4.7–11.5) | 27.3 (18.2–33.1) | 5.03 / 4.14 / — / 7.83 / — | UA ∝ duty, 0.0647 /K | no |
| `liquid_source_hx\|water_to_water\|coaxial\|condensing` | 240 / 2 / 1 | 6.08 (3.8–11.8) | 14.1 (10.1–18.9) | 5.49 / 4.30 / — / 3.74 / — | fixed ITD 14.15 K | no |
| `liquid_source_hx\|water_to_water\|coaxial\|evaporating` | 192 / 2 / 1 | 13.68 (7.0–18.7) | 16.4 (10.4–22.6) | 7.80 / 7.04 / — / 6.42 / — | UA ∝ duty, 0.0677 /K | no |
| `load_water_hx\|water_to_water\|coaxial\|condensing` | 188 / 2 / 1 | 4.52 (1.5–10.2) | 9.9 (6.4–14.3) | 4.99 / 4.45 / — / 3.81 / — | fixed ITD 9.93 K | no |
| `load_water_hx\|water_to_water\|coaxial\|evaporating` | 240 / 2 / 1 | 14.14 (6.6–18.9) | 19.2 (12.7–26.5) | 6.47 / 5.49 / — / 5.25 / — | UA ∝ duty, 0.0602 /K | no |
| `water_condenser\|bphe\|evaporating` | 8 / 1 / 1 | 3.89 (2.1–5.8) | 8.9 (7.1–11.0) | — (one group) | — | no |

The gate counts manufacturers as the sources name them; in the ducted evaporating stratum "Goodman" and
"Goodman/Amana (tested by Univ. of Maryland CEEE)" are the same company, so it rests on three, not four.

"—" in the RMSE column: the law needs a nameplate on every row, or four hold-out groups (linear), and the
stratum does not have them. In every stratum that meets the gate and is used by a model the leaving-end
approach is selected and the fixed inlet difference scores worst; in the one stratum where another law wins
(ducted indoor coils as evaporators, 8 rows, no model uses it) the leaving approach misses by 1.53 against
1.34 K. Manufacturer hold-out RMSE (each manufacturer predicted from the others) is in `regression.json`
→ `candidates.<law>.manufacturer_holdout`; for the outdoor condenser it is 1.84 K for the leaving approach
against 3.57 K for the fixed inlet difference.

The leaving approach closes as `UA = C ln(1 + Q / (C·dT_out))`, which has a solution for every positive duty
and flow (no runaway as `eps → 1`, no failure when the reference flow is 15 % lower than assumed).

**Duty per nameplate.** A default is written against a unit's nameplate, but the exchanger's own duty differs
from it. The build carries the median duty of each exchanger per watt of its unit's cooling and heating
nameplate (`band_p10_p50_p90.Q_hx_per_<mode>_nameplate`): the outdoor coil rejects 1.244 W per watt of
cooling nameplate as a condenser and absorbs 0.759 W per watt of heating nameplate as an evaporator. These
replace the fixed 0.8 and 0.7 duty ratios of the earlier rules.

**Role ratio.** The same coil of the same unit has a smaller effective conductance as an evaporator than as a
condenser (`role_ratios`): outdoor coils median 0.72 over 80 units (Mitsubishi Electric 0.76, one Lennox unit
0.21), ducted indoor coils 0.42–0.88 (three manufacturers), coaxial exchangers 0.49 (WaterFurnace), 0.36 and
0.54 (Bosch). The ratio is not used: each role takes the law of its own stratum, and both outdoor strata meet
the gate.

## What is not shipped, and why

**Wall-mounted indoor coils.** The stratum has 78 rows of 6 product lines but all from Mitsubishi Electric: the
only manufacturer whose tables give duty, airflow and pressure at one point. Toshiba and Gree print pipe-surface
temperatures and no duty at the point. The evidence also disagrees on the quantity the law uses:

- 6.85 K leaving approach on declared duty and airflow (what the stratum fits);
- 3.60 K on the printed discharge-air temperature of the same tables (median of `ITD − dT_fluid_printed`,
  product lines weighted equally) — the air-side heat, airflow × printed temperature rise, exceeds the declared
  capacity (median ratio 1.18, range 0.72–1.65);
- 3.3 K from Toshiba's pipe-surface temperatures at an assumed duty equal to the rated heating capacity
  (median of 32 rows).

The inlet difference itself agrees: 21.7 K for Mitsubishi, 22.0 K for Toshiba. So the inlet difference is
settled and the effectiveness is not, and `AirSourceHeatPump` keeps its earlier indoor rule (`UA_iu_rated =
0.16 W/K per W of nameplate`, recorded as provisional) instead of a value the evidence does not fix.

**Water-to-water units.** Four strata, 188–240 rows each, but from one manufacturer (Bosch WW/WT) whose heating
tables print suction pressures below the 40 psig low-pressure cut-out of the same manual (84 rows). The
liquid-source boilers borrow the water-to-air coaxial evaporator stratum instead.

**Immersed or wrapped tank coils.** No measured row pairs a condensing pressure with a tank temperature; the
only sources found are model outputs, which are never fitted. A boiler built without circulating water
(`m_dot_w_rated=0`) has to be given its `UA_tank_hx`.

**Wet cooling coils.** Indoor coils in cooling run wet; the model's coils are dry, so those rows are excluded
and the indoor values come from the dry condensing role.

## Conversions

**Pressure basis.** The basis is read from the printed unit (`psig`, `MPa(G)`, `bara` …). A unit that does not
state it is converted only when the basis is inferred from the same table or declared for the source:

- inferred (`infer_pressure_basis`): where the table prints a condensing or evaporating temperature next to
  the pressure, only one basis reproduces it — accepted when its median mismatch is below 1 K and the other's
  above 2 K. KTH (absolute, 21 rows, 0.01 K against 5.8 K) and the Mitsubishi Ecodan manuals OCH439, OCH562,
  OCH605 (gauge, 0.26–0.71 K against 2.2–2.4 K);
- declared (`pressure_basis` with `pressure_basis_evidence` in `sources.json`): the five AHRI AREP reports,
  whose report forms print every pressure as kPa with psia in the IP column (absolute);
- otherwise the row keeps its printed temperature or has no saturation temperature
  (`pressure_basis_not_stated`, 85 rows).

**Numbers.** `10,187` is a thousands separator; `26,7` and `8024,3` are decimal commas (AHRI report forms). A
decimal comma followed by exactly three digits cannot be told apart and is corrected in `errata.csv` (five
values of the KTH condenser design sheet). Ranges (`110-120`, `1.0 to 1.2`) enter at their midpoint, with the
ends kept as `T_sat_low_C` / `T_sat_high_C`.

**Saturation temperature.** The mean of bubble and dew point at the pressure (CoolProp), with the glide
recorded. CoolProp's R454B mixture flash fails above about 57 °C, where cooling-duty condensing pressures lie,
so for predefined mixtures the build fits `ln p = a + b/T + c ln T + d T` to CoolProp's flashes between 240 and
322 K (residual below 0.01 K) and uses it throughout.

**Printed condensing temperatures are not used where the pressure is.** In Mitsubishi Electric's R410A tables
the printed condensing temperature is the saturation temperature of the printed pressure (median difference
0.12 K, 227 rows). In its R454B tables (MUZ-GX, FX, JX, HX, WX; 57 rows above 20 °C) it is the dew point: it
sits 0.55 K above the mean saturation temperature the build uses and 1.16 K above the bubble point (glide
1.24 K). The pressure is used in both. One R454B manual (MUZ-FX) prints heating condensing temperatures of
37–39 °F that match neither, see the anomalies below.

**Fluids and flows.** Air: dry air at the inlet temperature and 1 atm. A liquid the document does not name is
taken as water (892 rows; the heat capacity per litre of the antifreeze mixtures ground loops use is within
about 5 % of it); a named antifreeze is evaluated as that mixture. A flow printed per ton of nominal capacity
(`gpm/ton`, 248 rows) is not converted, because the nominal tonnage would have to be decoded from the model
number; those rows give an inlet difference only.

## Hold-out groups

Product lines that share hardware count as one group in the hold-out and in the weighting
(`holdout_groups.json`); every other `family_id` is its own group. All of them are Mitsubishi Electric
North-American series whose tables repeat the same pressures, airflows and temperatures:

- MUZ-FH, MUZ-FS and MUFZ-KJ (shared outdoor and indoor hardware);
- MUZ-FE and MUZ-FD (FD is the predecessor of FE; same pressures and airflows; both are third-party copies
  not yet accepted, so the group is in no fit);
- MUZ-GE, GL, HM, WR and JP (identical data across series);
- MUZ-GX, JX, HX and WX (shared outdoor units);
- SUZ-KA in three manual generations (one outdoor line).

Toshiba's manuals repeat the same values across documents as well (for example the 18SKV/18SKVR/18N3KV/18N3KVR
units); those rows give an inlet difference only and enter no fit.

## Superseded revision

Revision A of the MUZ-FH manual (OBH684) is transcribed beside revision E to record the one value that changed
(the cooling indoor wet airflow, 381/398/411 CFM against 328/342/354); its 12 rows are excluded through
`errata.csv` (`superseded_revision`), and revision E is fitted.

## Evidence types and third-party copies

59 sources: 30 manufacturer PDFs from a manufacturer domain, 22 manufacturer PDFs from a third-party host, 7
public reports. Only the transcribed values, the source record and the SHA-256 are in Git. The 59 originals were
taken to the lab NAS on 2026-11-16 as research input, with the owner's approval of that day, third-party copies
included ([../nas-transfer-20261116.json](../nas-transfer-20261116.json): 59/59 SHA-256 matched on SMB read-back,
on the NAS shell and over NFS). None of them is in the #87 ledger, so `archive87_dataset_id` is null; their catalogue
metadata is bet-lab/dataset-catalog#107. Manufacturer manuals are copyrighted and kept for internal use only: the
NAS records each file's `licence` and `evidence_type` as written here. The rule of the parent README applies to third-party copies: a third-party copy is recorded as
`manufacturer_pdf_third_party_host` and enters a fit only once the owner has accepted the source against
manufacturer-domain documents that print the same values (`owner_accepted`, `owner_acceptance`). Since release
`hx-approach-2026-11-16.1` the build enforces it: rows of a third-party source without `owner_accepted: true` carry
`third_party_copy_not_accepted` in `exclusion_reason`, and `regression.json` → `third_party_sources_not_accepted`
lists those sources. No source carries `owner_accepted` yet, so all 22 are recorded and none is fitted. For five of
them (`MITSUBISHI-OBH498`, `MITSUBISHI-OBH502-REVE`, `MITSUBISHI-OBH543-REVG`, `MITSUBISHI-OBH821-REVA`,
`CARRIER-50PS-6SI`) this is the only reason their rows are out of the fits; they were fitted in the first release
and would return once accepted. The other third-party rows would stay excluded for their own reasons.

| Source | Host | Identical official copy | Rows that would enter the fits once accepted |
|---|---|---|---|
| `MITSUBISHI-OBH543-REVG` (MUZ-FE) | distributor CDN | none found on a manufacturer domain | 3 per outdoor stratum, 3 wall-mounted indoor |
| `MITSUBISHI-OBH498` (MUZ-FD, first edition) | Internet Archive copy of a ManualsOnline upload | none found; a later revision C seen behind a bot challenge, not obtained | 2 per outdoor stratum, 2 wall-mounted indoor |
| `MITSUBISHI-OBH502-REVE` (MUZ/MUY-D30/36) | distributor CDN | listed on the manufacturer's Canadian site, which refused connections; no archived copy, not compared | 2 per outdoor stratum, 2 wall-mounted indoor |
| `MITSUBISHI-OBH821-REVA` (MUZ-JP) | web-archive snapshot (original bytes) of the manufacturer's Canadian site | the live host refused connections; the snapshot is the manufacturer file but was not fetched from the manufacturer | 2 per outdoor stratum, 2 wall-mounted indoor |
| `CARRIER-50PS-6SI` (Aquazone 50PSH/PSV) | CDN path of unconfirmed ownership | none found on a carrier.com domain | 18 liquid condensing, 12 liquid evaporating, 8 water-to-air indoor |
| `MITSUBISHI-OBH684-REVA` (MUZ-FH, revision A) | inspection-site copy | revision E is official and fitted instead | 0 (superseded) |
| 14 Toshiba service manuals (`TOSHIBA-*`) | Russian distributor archive | none found on a Toshiba or Toshiba Carrier domain | 0 (pipe-surface temperatures, inlet difference only) |
| `CARRIER-SVM-12031` (Toshiba layout) | distributor CDN | none found on a carrier.com domain | 0 (inlet difference only) |
| `DANFOSS-VIGFI102` | HTML text rendition | official PDF not found | 0 (a check rule, not an operating point) |

Where a manufacturer-domain copy was found for a document first met on a third-party host, the official copy is
the source: the Mitsubishi manuals OBH684E, OBH873B, OBH875B and OBH819B are byte-identical (same SHA-256) to
the distributor copies; OBH951C, OBH753B and OBH549H print the same table values as their distributor copies
(the distributor's OBH549F omits one outdoor block that revision H has). The five AHRI AREP reports are
Wayback Machine copies of the AHRI-hosted PDFs (the current AHRI host is behind a bot challenge); KTH and ORNL
are read from their institutional repositories.

## Data-quality anomalies kept as printed

Values are transcribed as printed; contradictions are marked `ANOMALY(...)` (or `P-T CHECK`) in the row note and
handled by the exclusion rules or `errata.csv`, never by editing a value.

- Mitsubishi MUZ-FX (OBH946C, R454B): heating condensing temperatures of 37–39 °F in all six tables, matching
  neither the condensing pressure nor anything but the suction temperature; a letter "V" in one wet-bulb cell.
- Mitsubishi TCH022: PEAD-A18 heating condensing temperature 36 °F at 357 psig; the PEAD-A15 heating column
  prints an outdoor inlet of 70/47 °F (a shifted cell). Ten R410A rows (MUFZ-KJ, SEZ-KD09, MLZ-KP12, SVZ-KP12/18,
  PEAD-A18) print condensing temperatures 4–15 °F below the saturation temperature of their pressure.
- Mitsubishi R454B tables: suction-line temperatures below the dew point of the suction pressure (negative
  superheat down to −13 °F) in many rows; the suction temperature is not used.
- Mitsubishi MUZ-GL09: a dash for the heating outdoor wet bulb; MUZ-D and GS 30/36: capacities printed as two
  values without the condition; OBH684A: page footer misprinted "OBH584A".
- Toshiba: a cooling row of A08-010 named like its neighbour (13PKVP read as 10PKVP); RAS-13SKVR-E with
  63/66 rps against 75/80 rps for units with the same pressures and temperatures; the same RAS-18SKV-E stated at
  different conditions in two manuals. Several cooling outdoor rows give an effectiveness above one at the rated
  load, which is why Toshiba is used for the inlet difference only.
- Gree Vireo GEN3: heating outdoor coil temperatures above the stated −6.7 °C outdoor air; a heating capacity
  column printed in W that reads as Btu/h; identical tables for different sizes and series.
- Bosch WW/WT: heating suction pressures below the manual's own 40 psig low-pressure cut-out (84 rows); heating
  tables headed "rise (source) / drop (load)" as in cooling; WT061 heating suction 30–34 % below the series.
- WaterFurnace 7 Series: the 024 and 036 heating blocks are identical digit for digit; a 15 °F-wide air-rise
  range on 024/036; six rows where the condensing saturation temperature is below the leaving fluid. 5 Series
  500A11: the second-stage tables repeat the single-stage ones (32 rows).
- Carrier Aquazone 50PS: four rows where the discharge saturation temperature is below the leaving air of the
  paired table.
- ClimateMaster TR024: in cooling at 70 °F the water rise at 2.25 gpm/ton exceeds that at 1.5 gpm/ton.
- AHRI AREP: copied air-side cells (AREP-052 B and H3 inlets, AREP-032 heating evaporator block), an inlet
  pressure on the high side (AREP-054 heating), a sensible capacity above the total (AREP-032 test C),
  decimal commas (AREP-054). The comparison-data table is used where the air-side table contradicts it.
- KTH: the condenser design sheet's dew point (48.0 °C) and operating pressure (17.1 bara) disagree by about
  1.9 K; both are kept, and design sheets are never fitted.

## Running it

From the repository root. `audit` needs the evidence files under `validation/evidence/default_inputs/hx_approach/`
(not in Git); `build` and `export` do not. The originals are on the lab NAS, one dataset per source
(`energy-systems/<source-id>-source/files/source/`; `nas_path` in `sources.json`); copy them from there to run the
audit on another machine. `nas_check` reads the NAS copies and compares their SHA-256 with `sources.json`.

```bash
uv run python -m validation.default_inputs.hx_approach.build            # prints changes since the latest release
uv run --with pymupdf python -m validation.default_inputs.hx_approach.audit
uv run python -m validation.default_inputs.hx_approach.build --release hx-approach-YYYY-MM-DD --note "..."
uv run python -m validation.default_inputs.hx_approach.export           # latest release -> src/tmhp/data/hx_approach_regression.json
uv run python -m validation.default_inputs.nas_check                    # all three archives; --root /Volumes/datasets on a Mac
```

`export` refuses a `regression.json` that is not the latest release. The runtime reads the exported file through
`tmhp.default_inputs.estimate_hx_UA` and `hx_duty_per_nameplate`; strata that miss the gate are exported with
`meets_shipping_gate=false` and refused with the reason.

The audit checks that every source file has its recorded SHA-256 and page count, that every transcribed number
is printed in the source (14,303 on the cited page, 1,070 elsewhere in the same document, 2 in a non-PDF source,
7 listed in `unverifiable.json` — the two scanned KTH design sheets), and that `measurements.csv` and
`regression.json` are the build of the current observations.

## Releases

- `hx-approach-2026-11-16` (first release): 59 sources, 2,287 observation rows, 1,641 fitted; data SHA-256
  `19ff6b9297fa090055a320bc9cf888b60e143af40af72fdb2a38bbeb53b67a5c`. Shipped strata: the two outdoor-coil roles, the air-to-water plate condenser, the
  water-to-air coaxial exchanger in both roles, the water-to-air indoor coil, and the two ducted indoor-coil
  roles (archived, not used by a model). The five third-party copies of the next release were fitted pending acceptance.
- `hx-approach-2026-11-16.1`: third-party copies are fitted only once accepted (`third_party_copy_not_accepted`); five
  sources leave the fits (Mitsubishi Electric OBH498, OBH502-REVE, OBH543-REVG, OBH821-REVA; Carrier 50PS-6SI).
  59 sources, 2,287 observation rows, 1,576 fitted; data SHA-256
  `855fdbe2c09a7cc59f28193cc63b81d1fa948d9255a874d442819fb9f75d2b58`. Leaving approaches moved: outdoor coil evaporating 3.18 → 2.82 K; water-to-air indoor coil 2.45 → 2.79 K; coaxial condensing 3.92 → 4.06 K; outdoor coil condensing 2.76 → 2.73 K; wall-mounted indoor coil (not shipped) 6.28 → 6.85 K.
  The same strata ship; each still meets the gate.
