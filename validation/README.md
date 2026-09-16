# `validation/` — where TMHP's automatic choices come from

TMHP decides a lot on the user's behalf. Give it a capacity and a refrigerant
and it produces heat-exchanger conductances, a compressor displacement and a
rated air flow. That convenience is only worth having if each of those numbers
can be traced to a published document rather than to somebody's judgement, and
if a reader can rerun the step that produced it.

This directory is that trace. It holds three things:

| | what it is | tracked? |
|---|---|---|
| `evidence/` | the source documents themselves | **no** — publisher PDFs are not redistributable |
| `registry/sources.yaml` | what each document is, where to get it, its sha256 | yes |
| `extraction/`, `data/` | the scripts that read them and the numbers that come out | yes |

A reader who obtains the same document from the manufacturer and gets the same
checksum is running on identical bytes. Everything under `data/` then follows
from those bytes by the scripts in `extraction/`.

## The chain, in order

```
uv run python -m validation.extraction.dt_convention      # L1  the method is an identity
uv run python -m validation.extraction.en328_inversion    # L2/L3 evaporator practice
uv run python -m validation.extraction.env327_inversion   # L2/L3 condenser practice
uv run python -m validation.extraction.trane_geometry     # L5  independent geometric route
uv run python -m validation.extraction.ua_transfer_law    # L6/L7 the default itself
```

Each script prints its own result and writes a CSV under `data/`. They are
ordered but independent: any one can be rerun alone, and the last one fails
loudly if an earlier output is missing rather than silently using a stale file.

### Why it is built this way

Published literature reports the conductance of *particular machines*. Nobody
publishes a conductance-per-unit-capacity rule, which is what a model that
sizes its own heat exchangers actually needs. Component heat-exchanger
catalogues close that gap, because a manufacturer rates an entire range at one
declared condition: models of different capacity land on a common operating
point, so conductance per unit duty becomes comparable across the range by
construction.

Inverting such a catalogue is not a fit. When the refrigerant side changes
phase the capacity-rate ratio vanishes, the LMTD and effectiveness-NTU
statements coincide, and the declared triple (duty, air volume flow, rating
temperature difference) pins the conductance exactly. There is no parameter to
adjust, so the result is either right or the table was misread —
`dt_convention` proves the first part and the effectiveness guard in
`_inversion.ua_from_declared_rating` catches the second.

## Adding a catalogue

The process below is fixed: follow it and the documentation site picks the new
unit up on the next push to `main`, parity plot, error table and all.

1. **Put the document in `evidence/`** under `catalogs/`, `pdfs/`, `data/` or
   `screens/`. Nothing here is committed.
2. **Register it**: add an entry to the `DESCRIBED` map in
   `extraction/build_registry.py` — title, publisher, document id, the rating
   condition it declares, and where a reader can obtain it — then run
   `uv run python -m validation.extraction.build_registry`. A document with no
   entry is listed under `unused_archive`, which is the audit trail for
   "nobody has checked this file yet".
3. **Transcribe the operating grid** into `catalogs/<slug>.yaml` (see
   `catalogs/README.md` for the schema). One file per outdoor unit: nameplate,
   refrigerant, the declared displacement if the manufacturer publishes one,
   and the temperature grid with its target capacity and power.
4. **Run the parity harness**: `uv run python -m validation.parity.run --unit
   <slug>`. It applies TMHP's *default* rules unchanged — that is the point of
   the exercise — and writes `results/<slug>.csv` plus a line in
   `results/summary.csv`.
5. **Refresh the site data**: `uv run python scripts/data/gen_validation_data.py`.
6. **Commit** `catalogs/<slug>.yaml`, `registry/sources.yaml`, `results/`, and
   the regenerated `docs/source/_static/data/*.json`. Pushing to `main` rebuilds
   the documentation site.

Step 4 is where the discipline lives. A catalogue is a *validation* set, never
a calibration set: no parameter is tuned to the unit being tested. Values the
manufacturer publishes about the machine itself — displacement, rated air flow —
are inputs, not tuning. Everything else comes from the defaults derived here.

## What is deliberately not claimed

The rules derived here are established for dry, round-tube-plate-fin outdoor
coils of packaged equipment. Frosted operation, microchannel coils and the
residential mini-split product class lie outside the evidence. `trane_geometry`
prints the sensitivity of its result to every quantity it had to declare rather
than to read, and the largest of those — the refrigerant-side film coefficient —
moves the answer by −19 % to +59 %. That is the honest width of the geometric
route, and it is why the default is set by the route that does not need it.
