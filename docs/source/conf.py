# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# Add the src directory to sys.path so autodoc can find the TMHP package.
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, src_path)

# -- Project information -------------------------------------------------------

project = "TMHP"
copyright = "2025, betlab"
author = "Habin Jo, Wonjun Choi"
release = "0.1.0"

# -- General configuration -----------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "sphinx_design",
    "myst_parser",
    "sphinx.ext.githubpages",
    # UX/UI layer (see pyproject.toml docs group for the rationale).
    "sphinxcontrib.mermaid",
    "sphinx_togglebutton",
    "sphinxext.opengraph",
    "notfound.extension",
    "sphinx_sitemap",
    "sphinx_last_updated_by_git",
]

autosummary_generate = True
autosummary_generate_overwrite = False

templates_path = ["_templates"]
exclude_patterns = ["_build", "build", "Thumbs.db", ".DS_Store"]

suppress_warnings = ["myst.xref_missing"]

# -- MyST-parser extensions ---------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "dollarmath",
    "fieldlist",
    "html_admonition",
    "html_image",
    "replacements",
    "smartquotes",
    "strikethrough",
    "substitution",
    "tasklist",
]

# -- HTML output ---------------------------------------------------------------

html_theme = "shibuya"
html_title = "TMHP"
html_short_title = "TMHP"
html_static_path = ["_static"]
html_css_files = ["css/custom.css", "css/integration-diagrams.css"]

# Interactive-layer JS modules. Registered here (rather than injected via
# `_templates/page.html`) so Sphinx adds a content-hash `?v=` to each URL —
# without that, an edit to (say) `scroll-spy.js` would ride the browser's
# stale-cached copy until a hard refresh. Order matters: `global.js` runs
# after every module-defining file so the wiring side sees `window.tmhp*`.
_DEFER = {"defer": "defer"}
html_js_files = [
    ("js/core/glossary.js", _DEFER),
    ("js/core/cmdk.js", _DEFER),
    ("js/core/reading-progress.js", _DEFER),
    ("js/core/scroll-spy.js", _DEFER),
    ("js/core/global.js", _DEFER),
    ("js/widgets/integration-diagrams.js", _DEFER),
]
html_baseurl = "https://bet-lab.github.io/TMHP/"

# Hide the per-page "Show Source" link — the GitHub icon already gives readers
# a path to the repo, and the in-tree .rst files aren't a useful artifact.
html_show_sourcelink = False
html_copy_source = False

html_theme_options = {
    # Cool blue accent fits a thermodynamics / refrigerant-cycle library and
    # keeps headings, links, and the active-nav indicator on one consistent hue.
    "accent_color": "iris",
    "github_url": "https://github.com/bet-lab/TMHP",
    # Expand top-level toctree captions in the sidebar by default so the main
    # sections are visible without the reader hunting for them.
    "globaltoc_expand_depth": 1,
    "nav_links": [
        {"title": "Start", "url": "getting-started/index"},
        {"title": "Concepts", "url": "concepts/index"},
        {"title": "Models", "url": "models/index"},
        {"title": "Tutorials", "url": "tutorials/index"},
        {"title": "Integrations", "url": "integrations/index"},
        {"title": "API", "url": "api/index"},
        {"title": "Validation", "url": "validation/index"},
    ],
    # Social-card image shown when the docs are linked on GitHub, Slack, etc.
    "og_image_url": (
        "https://bet-lab.github.io/TMHP/"
        "_static/source_sink_matrix.svg"
    ),
}

# -- Autodoc ------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Optional co-simulation adapters depend on packages that are not part of the
# docs dependency group. Mock them so the API reference can document the adapter
# modules without forcing every docs build to install PythonFMU or EnergyPlus.
autodoc_mock_imports = ["pythonfmu", "pythonfmu3", "pyenergyplus"]

# -- Napoleon -----------------------------------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_param = True
napoleon_use_rtype = True

# -- Type hints ---------------------------------------------------------------

typehints_fully_qualified = False
always_document_param_types = True
typehints_document_rtype = True

# -- Mermaid ------------------------------------------------------------------

# Use the same iris/blue palette as the Shibuya accent so cycle diagrams sit
# next to the rest of the page instead of looking like an embedded screenshot.
mermaid_version = "11.4.1"
mermaid_init_js = (
    "mermaid.initialize({"
    "startOnLoad: true,"
    "theme: 'base',"
    "themeVariables: {"
    "  'primaryColor': '#eef2ff',"
    "  'primaryTextColor': '#1e1b4b',"
    "  'primaryBorderColor': '#6366f1',"
    "  'lineColor': '#6366f1',"
    "  'tertiaryColor': '#f5f3ff',"
    "  'fontFamily': 'inherit'"
    "}"
    "});"
)

# -- Open Graph (social cards) ------------------------------------------------

ogp_site_url = "https://bet-lab.github.io/TMHP/"
ogp_site_name = "Thermodynamic Models for Heat Pumps"
ogp_image = (
    "https://bet-lab.github.io/TMHP/"
    "_static/source_sink_matrix.svg"
)
ogp_image_alt = "TMHP source and sink family matrix"
ogp_use_first_image = True

# -- Sitemap ------------------------------------------------------------------

# html_baseurl is set above; sphinx-sitemap needs it to generate absolute URLs.
sitemap_url_scheme = "{link}"

# -- 404 page -----------------------------------------------------------------

# Render relative to the docs root rather than the failing page's directory so
# the sidebar / nav links resolve correctly on the served 404.
notfound_urls_prefix = "/tmhp/"

# -- Last updated by git ------------------------------------------------------

# Per-page "Last updated YYYY-MM-DD" footer. Falls back silently when the file
# isn't in git (e.g. autogenerated autosummary stubs). The landing page is
# also excluded — a date on the front matter reads as ambiguous (which file
# was last touched?) and adds nothing the reader can act on.
git_untracked_check_dependencies = False
git_exclude_patterns = ["api/generated/*", "index.rst"]
