"""Freeze the selected correlations into an archived coefficient version and
regenerate the constants block of ``src/tmhp/compressor_efficiency.py``.

Reads   validation/data/compressor_maps/{selected.json, fit_results.json,
        points_fit_ready.csv, loco_pooled.csv, loco_strata.csv, model_selection.csv}
Writes  validation/coefficients/<version>/{manifest.json, coefficients.json,
        data_sources.csv, fit_metrics.csv, loco_strata.csv, model_selection.csv}
        and rewrites the text between the GENERATED markers in the library module.

The library module is hand-written around the block; only the numbers inside
the markers come from here, so a reviewer can diff the evidence and the code.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import time
from importlib import metadata

import CoolProp.CoolProp as CP
import pandas as pd

from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT

MODULE = REPO_ROOT / "src" / "tmhp" / "compressor_efficiency.py"
BEGIN = "# --- BEGIN GENERATED coefficients"
END = "# --- END GENERATED coefficients ---"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def build_coefficients(version: str) -> dict:
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fit = json.loads((DATA_DIR / "fit_results.json").read_text())
    vol = next(r for r in fit["eta_vol"] if r["family"] == sel["eta_vol"]["family"])
    g_key, s_key = sel["eta_oi"]["family"].split("x")[:2]
    oi = next(r for r in fit["eta_oi"] if r["g"] == g_key and r["s"] == s_key)
    anchor = sel["eta_em_anchor"]["anchor"]
    if vol["family"] != "V2" or g_key != "I2" or s_key not in ("E1", "E2"):
        raise SystemExit(
            f"emit_coefficients encodes V2 / I2 x (E1|E2); selection is {vol['family']} / {g_key}x{s_key} -- extend the module first"
        )
    d = float(oi["theta_s"].get("d", 0.0))  # E1 is E2 with no roll-off (ETA_EM_D = 0)
    return {
        "version": version,
        "eta_vol": {
            "family": "V2",
            "formula": "eta_vol = 1 - ETA_VOL_A*(PR-1) - ETA_VOL_B*max(0, 1/n* - 1)",
            "ETA_VOL_A": vol["theta"]["a"],
            "ETA_VOL_B": vol["theta"]["b"],
            "floor": 0.50,
            "fit_wrmse": vol["wrmse"],
            "fit_wmape_pct": vol["wmape_pct"],
            "loco_wmape_pct": sel["eta_vol"]["loco_wmape_pct"],
            "legacy_loco_wmape_pct": sel["eta_vol"]["legacy_loco_wmape_pct"],
        },
        "eta_oi": {
            "family": f"I2x{s_key}",
            "formula": "eta_oi = (ETA_OI_A - ETA_OI_B*PR - ETA_OI_C/PR) * n*(1+ETA_EM_N0)/(n*+ETA_EM_N0) * (1 - ETA_EM_D*max(0, min(n*, N_STAR_EM_MAX) - 1)^2)",
            "ETA_OI_A": oi["theta_g"]["A"],
            "ETA_OI_B": oi["theta_g"]["B"],
            "ETA_OI_C": oi["theta_g"]["C"],
            "ETA_EM_N0": oi["theta_s"]["n0"],
            "ETA_EM_D": d,
            "N_STAR_EM_MAX": 2.0,
            "fit_wrmse": oi["wrmse"],
            "fit_wmape_pct": oi["wmape_pct"],
            "loco_wmape_pct": sel["eta_oi"]["loco_wmape_pct"],
            "legacy_loco_wmape_pct": sel["eta_oi"]["legacy_loco_wmape_pct"],
            "tie_break": sel["eta_oi"].get("tie_break", ""),
            "extension_candidates_not_adopted": sel["eta_oi"].get("extension_candidates", []),
            "rejected_by_R4_within_machine_identification": sel["eta_oi"].get("rejected_by_R4", []),
            "R4_ratios": sel["eta_oi"].get("R4", {}),
        },
        "split": {
            "ETA_EM_REF": anchor,
            "basis": "median measured eta_em at n*~1, inverter-fed, discharge-temperature split (Cuevas & Lebrun 2009 Table 3)",
            "n": sel["eta_em_anchor"]["n"],
            "p10": sel["eta_em_anchor"]["p10"],
            "p90": sel["eta_em_anchor"]["p90"],
            "eta_isen_formula": "eta_isen = max(ETA_ISEN_FLOOR, (ETA_OI_A - ETA_OI_B*PR - ETA_OI_C/PR) / ETA_EM_REF)",
            "eta_em_formula": "eta_em = ETA_EM_REF * n*(1+ETA_EM_N0)/(n*+ETA_EM_N0) * (1 - ETA_EM_D*max(0, min(n*, N_STAR_EM_MAX) - 1)^2)",
            "ETA_ISEN_FLOOR": 0.30,
        },
        "separability": fit["separability"],
        "spread_eta_vol": fit["spread_eta_vol"],
        "spread_eta_oi": fit["spread_eta_oi"],
        "n_rows": fit["n_rows"],
        "machines": fit["machines"],
        "speed_records": fit["speed_records"],
    }


def render_block(c: dict) -> str:
    v, o, s = c["eta_vol"], c["eta_oi"], c["split"]
    return f"""{BEGIN} {c["version"]} ---
#: Coefficient version; the archive with data list, fits and cross-validation
#: lives in ``validation/coefficients/{c["version"]}/``.
COEFFICIENT_VERSION = "{c["version"]}"
#: Volumetric efficiency ``1 - A (PR - 1) - B max(0, 1/n* - 1)`` -- {c["machines"]} machines,
#: {c["speed_records"]} speed records, LOCO MAPE {v["loco_wmape_pct"]:.2f} % (legacy {v["legacy_loco_wmape_pct"]:.2f} %).
ETA_VOL_A = {v["ETA_VOL_A"]:.5f}
ETA_VOL_B = {v["ETA_VOL_B"]:.5f}
#: Electrical-to-isentropic product
#: ``(A - B PR - C/PR) * n*(1+n0)/(n*+n0) * (1 - D max(0, n*-1)^2)`` --
#: LOCO MAPE {o["loco_wmape_pct"]:.2f} % (legacy {o["legacy_loco_wmape_pct"]:.2f} %).
ETA_OI_A = {o["ETA_OI_A"]:.5f}
ETA_OI_B = {o["ETA_OI_B"]:.5f}
ETA_OI_C = {o["ETA_OI_C"]:.5f}
ETA_EM_N0 = {o["ETA_EM_N0"]:.5f}
ETA_EM_D = {o["ETA_EM_D"]:.5f}
#: Electro-mechanical efficiency at rated speed: the measured split of the
#: product (Cuevas & Lebrun 2009, inverter-fed, n* ~ 1; p10-p90 {s["p10"]:.3f}-{s["p90"]:.3f}).
ETA_EM_REF = {s["ETA_EM_REF"]:.4f}
{END}"""


def rewrite_module(block: str) -> None:
    text = MODULE.read_text()
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit("GENERATED markers not found in compressor_efficiency.py")
    MODULE.write_text(pattern.sub(lambda _m: block, text))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=time.strftime("v%Y-%m-%d"))
    ap.add_argument("--no-module", action="store_true", help="archive only; do not touch the library module")
    a = ap.parse_args()
    c = build_coefficients(a.version)
    out = REPO_ROOT / "validation" / "coefficients" / a.version
    out.mkdir(parents=True, exist_ok=True)
    (out / "coefficients.json").write_text(json.dumps(c, indent=1))
    manifest = {
        "version": a.version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tmhp": metadata.version("tmhp") if _installed("tmhp") else "src",
        "coolprop": CP.get_global_param_string("version"),
        "python": platform.python_version(),
        "git_head": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "src", "validation")),
        "selection_rule": "sequential nesting: +1 coefficient must buy >= 0.1 pp LOCO MAPE; no stratum with >= 3 machines worse by > 2 pp or > 25 %; "
        "constraints 0 < eta <= 1.02 and d(n* eta_vol)/dn* > 0 on PR 1.5-8, n* 0.15-2.5; a speed term must be seen within the machines that identify it "
        "(median within-machine/fitted ratio >= 2/3, >= 3 machines from >= 2 sources); ties within 0.1 pp go to the form with a physical precedent; "
        "PR x speed interaction kept as a documented extension only",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    df = pd.read_csv(DATA_DIR / "points_fit_ready.csv", low_memory=False)
    ok = df[(~df.exclude_fixed) & df.point_ok]
    src = (
        ok.groupby(
            [
                "source_id",
                "compressor_key",
                "manufacturer",
                "model",
                "comp_type",
                "refrigerant",
                "V_disp_cm3",
                "N_rated_rps",
                "N_rated_basis",
                "method",
                "vdisp_suspect",
            ],
            dropna=False,
        )
        .agg(
            n_points=("PR", "size"),
            speeds=("N_rps", "nunique"),
            n_star_min=("n_star", "min"),
            n_star_max=("n_star", "max"),
            PR_min=("PR", "min"),
            PR_max=("PR", "max"),
            doc=("doc_path", "first"),
        )
        .reset_index()
    )
    src.to_csv(out / "data_sources.csv", index=False)
    for name in ("loco_pooled.csv", "loco_strata.csv", "model_selection.csv"):
        shutil.copy(DATA_DIR / name, out / name)
    (DATA_DIR / "fit_results.json").rename(DATA_DIR / "fit_results.json") if False else shutil.copy(
        DATA_DIR / "fit_results.json", out / "fit_results.json"
    )
    if not a.no_module:
        rewrite_module(render_block(c))
        print(f"module block regenerated for {a.version}")
    print(f"archive -> {out}")


def _installed(name: str) -> bool:
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


if __name__ == "__main__":
    main()
