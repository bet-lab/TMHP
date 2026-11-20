# Minimal Makefile for Sphinx docs (cross-platform)
SPHINXOPTS    ?=
SPHINXBUILD   ?= uv run sphinx-build
PYTHON        ?= uv run python
SOURCEDIR     = source
BUILDDIR      = build
REPOROOT      = $(abspath $(CURDIR)/..)
DOCS_DATA_PROFILE ?= full

.PHONY: help Makefile html clean data

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# Build-time JSON data consumed by the interactive UX layer.
# Heavy cycle data is guarded by a profile-specific stamp so repeated local
# builds and CI cache hits do not solve the full CoolProp grid again.
DATA_DIR = $(REPOROOT)/docs/source/_static/data
WIDGET_DIR = $(REPOROOT)/docs/source/_static/widgets
GLOSSARY_DATA_JSON = $(DATA_DIR)/glossary.json
VALIDATION_DATA_JSON = $(DATA_DIR)/validation-points.json
VALIDATION_SUMMARY_JSON = $(DATA_DIR)/validation-summary.json
TIMESERIES_DATA_JSON = $(DATA_DIR)/timeseries-24h.json
CYCLE_DATA_JSON = $(WIDGET_DIR)/cycle_data.json
CYCLE_DATA_STAMP = $(WIDGET_DIR)/cycle_data.$(DOCS_DATA_PROFILE).stamp
DATA_TARGETS = \
	$(GLOSSARY_DATA_JSON) \
	$(CYCLE_DATA_JSON) \
	$(VALIDATION_DATA_JSON) \
	$(VALIDATION_SUMMARY_JSON) \
	$(TIMESERIES_DATA_JSON)
DATA_SCRIPT_SOURCES = $(wildcard $(REPOROOT)/scripts/data/*.py)
TMHP_SOURCES = $(wildcard $(REPOROOT)/src/tmhp/*.py $(REPOROOT)/src/tmhp/data/*.json)
CYCLE_SOURCES = $(REPOROOT)/scripts/data/gen_refrigerant_data.py $(REPOROOT)/src/tmhp/calc_util.py \
                $(REPOROOT)/src/tmhp/mollier_diagram.py $(REPOROOT)/src/tmhp/refrigerant.py
BUILD_INPUTS = $(REPOROOT)/uv.lock $(REPOROOT)/pyproject.toml
# The site's validation data is now derived from the parity harness output
# rather than recomputed here, so it depends on the results CSVs. Rerun
# `uv run python -m validation.parity.run` after adding or editing a
# catalogue; the docs build picks the new numbers up from validation/results/.
# Directory mtimes also pick up deleted catalogues or per-unit CSVs.
VALIDATION_SOURCES = $(REPOROOT)/validation/catalogs $(REPOROOT)/validation/results \
                     $(wildcard $(REPOROOT)/validation/parity/*.py $(REPOROOT)/validation/catalogs/*.yaml \
                                $(REPOROOT)/validation/results/*.csv)

# Git checkout times cannot establish whether tracked JSON matches its inputs.
# CI regenerates these three inexpensive datasets even on a cycle-grid cache hit.
ifeq ($(DOCS_FRESH_DATA),1)
.PHONY: $(GLOSSARY_DATA_JSON) $(VALIDATION_DATA_JSON) $(TIMESERIES_DATA_JSON)
endif
# The validation generator writes both outputs in one invocation.
ifeq ($(wildcard $(VALIDATION_SUMMARY_JSON)),)
.PHONY: $(VALIDATION_DATA_JSON)
endif
# The single published grid belongs to one profile. A missing or ambiguous
# stamp forces a solve even when both builds start in the same clock tick.
ifneq ($(wildcard $(WIDGET_DIR)/cycle_data.*.stamp),$(CYCLE_DATA_STAMP))
.PHONY: $(CYCLE_DATA_JSON)
endif

data: $(DATA_TARGETS)

# Prevent the generic Sphinx builder pattern below from treating Python source
# prerequisites as make targets when those files are older than this Makefile.
$(DATA_SCRIPT_SOURCES) $(TMHP_SOURCES) $(VALIDATION_SOURCES) $(BUILD_INPUTS):
	@:

$(GLOSSARY_DATA_JSON): $(REPOROOT)/scripts/data/gen_glossary.py $(REPOROOT)/scripts/data/_common.py
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_glossary

$(CYCLE_DATA_JSON): $(CYCLE_SOURCES) $(BUILD_INPUTS)
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_refrigerant_data --profile "$(DOCS_DATA_PROFILE)" "$@.tmp.json"
	rm -f "$(WIDGET_DIR)"/cycle_data.*.stamp
	mv "$@.tmp.json" "$@"
	touch "$(CYCLE_DATA_STAMP)"

$(VALIDATION_SUMMARY_JSON): $(VALIDATION_DATA_JSON)
	@:

$(VALIDATION_DATA_JSON): $(REPOROOT)/scripts/data/gen_validation_data.py $(REPOROOT)/scripts/data/_common.py $(VALIDATION_SOURCES) $(BUILD_INPUTS)
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_validation_data

$(TIMESERIES_DATA_JSON): $(REPOROOT)/scripts/data/gen_timeseries_data.py $(REPOROOT)/scripts/data/_common.py $(TMHP_SOURCES) $(BUILD_INPUTS)
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_timeseries_data

html: data
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

clean:
	rm -rf $(BUILDDIR)/*

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
