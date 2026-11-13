# Compressor displacement archive

Catalogue geometric displacement [cm³/rev] against compressor cooling capacity, per refrigerant and
rating protocol. The latest release ships in the library as
`src/tmhp/data/compressor_displacement_regression.json` and is used by the
`single_split_displacement_regression` default-input profile (issue #86). Evidence, results and limits:
[REPORT.md](REPORT.md).

## What is edited by hand

| File | One entry per | Rule |
|---|---|---|
| `sources.json` | document | URL, evidence path, SHA-256, page count, access date; `row_defaults` per protocol for facts the document states once (ambient, condition page, assumptions); `displacement_decoding` when the value is a model-code rule |
| `protocols.json` | rating protocol | refrigerant-side conditions, speed basis, compressor type, and the printed condition labels that map to it |
| `observations/<source_id>.csv` | printed table row | values exactly as printed; one file per source; never derived or rescaled |
| `errata.csv` | correction | `from_value` must match the current cell, with page evidence and reason; `field=exclude` drops a row with a reason |

Everything else here (`measurements.*`, `regression.json`, `audit.json`) is generated. `releases.json` is
appended by the build, never edited.

Observation columns: `record_id, source_pdf_page, printed_page, locator, manufacturer, family_id, model_id,
refrigerant, protocol, test_condition, capacity_W, capacity_Btu_h, power_W, displacement_text,
displacement_basis, note`. `displacement_basis` is `printed` or a key of the source's
`displacement_decoding`. `family_id` is the product series; held-out validation removes a whole family.

## Adding data

1. Store the original file under `validation/evidence/default_inputs/` (not in Git) and add the source to
   `sources.json` with its SHA-256 and page count.
2. Transcribe rows into `observations/<source_id>.csv`. Image-only PDFs: check every value against the
   rendered page. Machine-readable LG/Highly sources: `harvest/harvest.py` writes candidates to compare.
   Rows whose condition matches no protocol stay out; add a protocol only when its conditions are printed.
3. Build and read the changes it prints against the latest release:
   `uv run python -m validation.default_inputs.compressor_displacement.build`
4. Audit against the evidence files:
   `uv run python -m validation.default_inputs.compressor_displacement.audit`
5. Record the release, then export it to the package:
   `uv run python -m validation.default_inputs.compressor_displacement.build --release displacement-vYYYY-MM-DD --note "..."`
   `uv run python -m validation.default_inputs.compressor_displacement.export`
6. `uv run pytest tests/test_displacement_regression.py` fails if the package and the latest release differ.

A correction to an existing row goes in `errata.csv`, not into the observation file, so the printed value,
the corrected value and the evidence stay side by side.

## Built-in checks

- Columns, unique `record_id`, a known source per file, and a printed condition label valid for the protocol.
- Dual-unit capacity: W and Btu/h columns more than 2 % apart exclude the row (both values kept).
- Duplicate printed observations (same maker, model, refrigerant, protocol, capacity, displacement) stop the build.
- Audit: evidence hashes and page counts, page numbers within the document, protocol fields, decoding
  arithmetic, and that no held-out fold appears in its own training set.
