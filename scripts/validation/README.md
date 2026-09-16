# `scripts/validation/`

Figures for the documentation's validation pages. They read
`validation/results/`, which the parity harness writes:

```bash
uv run python -m validation.parity.run          # produce the results
uv run python -m scripts.validation.parity_figure
uv run python -m scripts.validation.part_load_figure
```

The unit-specific script that used to live here (`samsung_ehs_parity.py`) is
gone. It carried its own copy of the model parameters, and that copy had
drifted away from the library defaults — so the error figures published on the
site were not the errors a user of the library would have got. The catalogue it
covered is now `validation/catalogs/samsung_ehs_mono_ht_r32_14kw.yaml` and runs
through the same harness as every other unit, against the shipped defaults.

See `validation/README.md` for how to add a machine.
