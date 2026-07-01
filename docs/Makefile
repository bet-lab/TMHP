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
TIMESERIES_DATA_JSON = $(DATA_DIR)/timeseries-24h.json
CYCLE_DATA_JSON = $(WIDGET_DIR)/cycle_data.json
CYCLE_DATA_STAMP = $(WIDGET_DIR)/cycle_data.$(DOCS_DATA_PROFILE).stamp
DATA_TARGETS = \
	$(GLOSSARY_DATA_JSON) \
	$(CYCLE_DATA_JSON) \
	$(VALIDATION_DATA_JSON) \
	$(TIMESERIES_DATA_JSON)
DATA_SCRIPT_SOURCES = $(wildcard $(REPOROOT)/scripts/data/*.py)
TMHP_SOURCES = $(wildcard $(REPOROOT)/src/tmhp/*.py)
VALIDATION_SOURCES = $(wildcard $(REPOROOT)/scripts/validation/*.py)

data: $(DATA_TARGETS)

# Prevent the generic Sphinx builder pattern below from treating Python source
# prerequisites as make targets when those files are older than this Makefile.
$(DATA_SCRIPT_SOURCES) $(TMHP_SOURCES) $(VALIDATION_SOURCES):
	@:

$(GLOSSARY_DATA_JSON): $(REPOROOT)/scripts/data/gen_glossary.py $(REPOROOT)/scripts/data/_common.py
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_glossary

$(CYCLE_DATA_JSON): $(CYCLE_DATA_STAMP)
	@:

$(CYCLE_DATA_STAMP): $(REPOROOT)/scripts/data/gen_refrigerant_data.py $(REPOROOT)/src/tmhp/calc_util.py $(REPOROOT)/src/tmhp/mollier_diagram.py $(REPOROOT)/src/tmhp/refrigerant.py
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_refrigerant_data --profile "$(DOCS_DATA_PROFILE)" "$@.tmp.json"
	mv "$@.tmp.json" "$(CYCLE_DATA_JSON)"
	touch "$@"

$(VALIDATION_DATA_JSON): $(REPOROOT)/scripts/data/gen_validation_data.py $(REPOROOT)/scripts/data/_common.py $(VALIDATION_SOURCES) $(TMHP_SOURCES)
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_validation_data

$(TIMESERIES_DATA_JSON): $(REPOROOT)/scripts/data/gen_timeseries_data.py $(REPOROOT)/scripts/data/_common.py $(TMHP_SOURCES)
	cd "$(REPOROOT)" && $(PYTHON) -m scripts.data.gen_timeseries_data

html: data
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

clean:
	rm -rf $(BUILDDIR)/*

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
