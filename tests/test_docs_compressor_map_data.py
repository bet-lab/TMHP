"""Agreement tests for the compressor-map widget's data (#253).

Contract: ``docs/notes/compressor-map-widget-contract.md`` ("Agreement test").

1. The tracked JSON's version, coefficients, domain and measured points equal the library and ``points.csv``.
2. Regenerating the JSON reproduces the tracked file byte for byte.
3. ``node`` evaluates every check point through the widget's pure evaluator and matches the JSON.

Item 3 assumes this interface of ``docs/source/_static/js/widgets/compressor-map-model.js`` (an ES module,
owned by the widget worker)::

    export function evaluate(data, PR, N_rps, T_evap_C)
        -> { eta_v, eta_ov, f_loss, eta_is, in_domain }      // null for a value that does not exist

``data`` is the parsed JSON. Skip rules: the whole item skips when the JS module is not present; once it is,
a missing ``node`` skips locally but fails when ``CI`` is set (GitHub-hosted Ubuntu runners ship ``node``).
A module that is an ES module in a ``.js`` file needs a node that detects module syntax (22.7+; on by default
from 22.12).
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.data import _common, gen_compressor_map_data

from tmhp.compressor_efficiency import COEFFICIENT_VERSION, DOMAIN, MAP_COEFFICIENTS

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_JSON = REPO_ROOT / "docs" / "source" / "_static" / "data" / "compressor-maps.json"
MODEL_JS = REPO_ROOT / "docs" / "source" / "_static" / "js" / "widgets" / "compressor-map-model.js"
POINTS_CSV = REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "points.csv"

REQUIRED_NAMED_POINTS = [
    (3.785, 70.0, 0.0),
    (1.5, 70.0, 25.0),
    (1.0, 70.0, 0.0),
    (1.2, 70.0, 0.0),
    (3.0, 15.0, 0.0),
    (3.0, 120.0, 0.0),
    (12.0, 70.0, -30.0),
    (3.0, 70.0, -10.0),
    (3.0, 70.0, 15.0),
]


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


# --- item 1: the JSON equals the library and points.csv --------------------------------------------------


def test_version_coefficients_and_domain_equal_the_library(data):
    assert data["schema"] == 1
    assert data["coefficient_version"] == COEFFICIENT_VERSION
    assert data["coefficients"] == {name: list(values) for name, values in MAP_COEFFICIENTS.items()}
    assert {name: len(values) for name, values in data["coefficients"].items()} == {
        name: len(terms) for name, terms in data["terms"].items()
    }
    assert data["domain"]["N_rps"] == list(DOMAIN.rps_range)
    assert data["domain"]["hull_PR_Te"] == [list(v) for v in DOMAIN.hull_PR_Te.vertices]


def test_measured_points_equal_points_csv(data):
    with POINTS_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 134
    expected = [
        {
            "id": row["point_id"],
            **{key: float(row[key]) for key in ("T_evap_C", "T_cond_C", "N_rps", "PR", "eta_v", "eta_ov", "f_loss")},
        }
        for row in rows
    ]
    assert data["measured_points"] == expected


def test_peak_is_the_interior_eta_ov_maximum_of_the_data_domain(data):
    from tmhp.compressor_efficiency import map_eta_ov

    peak = data["peak_eta_ov"]
    n_lo, n_hi = DOMAIN.rps_range
    prs = [pr for pr, _ in DOMAIN.hull_PR_Te.vertices]
    assert min(prs) < peak["PR"] < max(prs)
    assert n_lo < peak["N_rps"] < n_hi
    best = map_eta_ov(peak["PR"], peak["N_rps"], 0.0)
    # No neighbouring point on a coarse ring is higher (it is a maximum, not just a stationary point).
    for dpr in (-0.05, 0.0, 0.05):
        for dn in (-0.5, 0.0, 0.5):
            assert map_eta_ov(peak["PR"] + dpr, peak["N_rps"] + dn, 0.0) <= best + 1e-12


def test_check_points_cover_the_contract_cases_and_equal_the_library(data):
    points = data["check_points"]
    keys = {(p["PR"], p["N_rps"], p["T_evap_C"]) for p in points}
    for case in REQUIRED_NAMED_POINTS:
        assert case in keys, case
    vertices = {(pr, t) for pr, t in DOMAIN.hull_PR_Te.vertices}
    assert any((p["PR"], p["T_evap_C"]) in vertices and p["in_domain"] for p in points), "a hull vertex, inside"
    grid = [p for p in points if 1.3 <= p["PR"] <= 12 and 15 <= p["N_rps"] <= 120 and -30 <= p["T_evap_C"] <= 25]
    assert len(grid) - len(REQUIRED_NAMED_POINTS) >= 20
    for p in points:
        assert p == gen_compressor_map_data.check_point(p["PR"], p["N_rps"], p["T_evap_C"])
    by_case = {(p["PR"], p["N_rps"], p["T_evap_C"]): p for p in points}
    assert by_case[(1.5, 70.0, 25.0)]["eta_v"] > 1.0
    assert by_case[(1.2, 70.0, 0.0)]["in_domain"] is False
    assert by_case[(3.0, 15.0, 0.0)]["in_domain"] is False
    assert by_case[(3.0, 120.0, 0.0)]["in_domain"] is False
    assert by_case[(3.785, 70.0, 0.0)]["in_domain"] is True


# --- item 2: regeneration is byte-identical --------------------------------------------------------------


def test_regenerating_reproduces_the_tracked_file_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.setattr(_common, "DATA_DIR", tmp_path)
    gen_compressor_map_data.main()
    assert (tmp_path / "compressor-maps.json").read_bytes() == DATA_JSON.read_bytes()


# --- item 3: the JS evaluator reproduces the check points ------------------------------------------------

_NODE_RUNNER = """
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
const [modulePath, dataPath] = process.argv.slice(1);
const { evaluate } = await import(pathToFileURL(modulePath).href);
const data = JSON.parse(readFileSync(dataPath, "utf8"));
const out = data.check_points.map((p) => evaluate(data, p.PR, p.N_rps, p.T_evap_C));
process.stdout.write(JSON.stringify(out));
"""

_NUMERIC = ("eta_v", "eta_ov", "f_loss", "eta_is")


def _close(got, want) -> bool:
    if want is None or got is None:
        return want is None and got is None
    return math.isfinite(got) and abs(got - want) <= 1e-12 + 1e-9 * abs(want)


def test_js_evaluator_reproduces_the_check_points(data, tmp_path):
    assert MODEL_JS.exists(), f"{MODEL_JS} is missing: the widget cannot evaluate the maps"
    model_mjs = tmp_path / "compressor-map-model.mjs"
    shutil.copyfile(MODEL_JS, model_mjs)
    node = shutil.which("node")
    if node is None:
        message = "node not found (needed to evaluate compressor-map-model.js)"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    run = subprocess.run(
        # A copy with the .mjs extension imports as an ES module on every node that has ES modules,
        # without relying on module-syntax detection for a .js file (on by default only from 22.12).
        [node, "--input-type=module", "-e", _NODE_RUNNER, str(model_mjs), str(DATA_JSON)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    results = json.loads(run.stdout)
    assert len(results) == len(data["check_points"])

    problems = []
    for point, got in zip(data["check_points"], results, strict=True):
        label = (point["PR"], point["N_rps"], point["T_evap_C"])
        if got.get("in_domain") != point["in_domain"]:
            problems.append(f"{label}: in_domain {got.get('in_domain')!r} != {point['in_domain']!r}")
        problems += [
            f"{label}: {key} {got.get(key)!r} != {point[key]!r}"
            for key in _NUMERIC
            if not _close(got.get(key), point[key])
        ]
    assert not problems, "\n".join(problems)
