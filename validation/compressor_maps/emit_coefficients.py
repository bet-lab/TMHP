"""Freeze the selected correlations into an archived coefficient version and
regenerate the constants block of ``src/tmhp/compressor_efficiency.py``.

Reads   validation/data/compressor_maps/{selected.json, fit_results.json, split.json,
        points_fit_ready.csv, loco_pooled.csv, loco_strata.csv, model_selection.csv,
        within_machine_contrasts.csv}
Writes  validation/coefficients/<version>/{manifest.json, coefficients.json,
        data_sources.csv, fit_results.json, split.json, loco_pooled.csv,
        loco_strata.csv, model_selection.csv, within_machine_contrasts.csv}
        and rewrites the text between the GENERATED markers in the library module.

Structure of the shipped correlations (see the module docstring)::

    eta_vol  = 1 - A (PR-1) - B u - C (PR-1) u
    eta_oi   = g(PR) s(n*) x(PR,n*) h(n*)
    eta_em   = ETA_EM_REF s(n*) m(PR)
    eta_isen = eta_oi / eta_em

The volumetric family may be V2 (C = 0), V6 (B = 0) or V7.  The product family
must carry the *anchored* drive factor (F0/F2/F3, or G0/G2 for the drive-only
anchor), the I2 lift shape, and X0/L1/L2 for the leakage interaction; E-families
(free n0) are not emitted because the split needs the drive factor to be the
one measured on the discharge-temperature rows.  ``--mode`` picks the
estimator whose parameters are written (default: the one ``selected.json``
names, else ``fe``).

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

from validation.compressor_maps.fit import split_key
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT

MODULE = REPO_ROOT / "src" / "tmhp" / "compressor_efficiency.py"
BEGIN = "# --- BEGIN GENERATED coefficients"
END = "# --- END GENERATED coefficients ---"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _installed(name: str) -> bool:
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def build_coefficients(
    version: str,
    mode: str | None,
    vol_override: str | None,
    oi_override: str | None,
    em_anchor: str = "drive",
    note: str = "",
) -> dict:
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fit = json.loads((DATA_DIR / "fit_results.json").read_text())
    split = json.loads((DATA_DIR / "split.json").read_text())
    mode = mode or sel.get("mode", "fe")
    vol_key = vol_override or sel[f"eta_vol:{mode}"]["family"]
    oi_key = oi_override or sel[f"eta_oi:{mode}"]["family"]
    g_key, s_key, x_key = split_key(oi_key)
    if vol_key not in ("V2", "V6", "V7"):
        raise SystemExit(f"emit_coefficients encodes V2/V6/V7 for eta_vol; selection is {vol_key}")
    if g_key != "I2" or s_key not in ("E0", "F0", "F2", "F3", "G0", "G2") or x_key not in ("X0", "L1", "L2"):
        raise SystemExit(f"emit_coefficients encodes I2 x (E0|F0|F2|F3|G0|G2) x (X0|L1|L2); selection is {oi_key}")
    vol_recs = fit["eta_vol_fe"] if mode == "fe" else fit["eta_vol"]
    vol = next(r for r in vol_recs if r["family"] == vol_key)
    oi_recs = fit["eta_oi_fe"] if mode == "fe" else fit["eta_oi"] + fit["eta_oi_x"]
    oi = next(r for r in oi_recs if r["key"] == oi_key)
    # drive anchor of eta_em: the family's own anchor for F/G, else the requested one (default: drive-only floor)
    anchor = "total" if s_key.startswith("F") else ("drive" if s_key.startswith("G") else em_anchor)
    n0 = split["ETA_EM_N0"] if anchor == "total" else split["ossorio_drive_only"]["n0_drive_median"]
    d = float(oi["theta_s"].get("d", 0.0))
    flow_two_sided = s_key in ("F2", "G2")
    c = float(oi["theta_x"].get("c", 0.0))
    leak_two_sided = x_key == "L2"
    selv, selo = sel[f"eta_vol:{mode}"], sel[f"eta_oi:{mode}"]
    # cross-validation metrics of the *emitted* families (which may differ from the automatic pick)
    loco = pd.read_csv(DATA_DIR / "loco_pooled.csv")
    loco = loco[loco["mode"] == mode].set_index(["kind", "family"])
    mv, mo = loco.loc[("eta_vol", vol_key)], loco.loc[("eta_oi", oi_key)]
    return {
        "version": version,
        "estimator": mode,
        "decision_note": note,
        "eta_vol": {
            "family": vol_key,
            "formula": "eta_vol = 1 - ETA_VOL_A*(PR-1) - ETA_VOL_B*u - ETA_VOL_C*(PR-1)*u, u = max(0, 1/n* - 1)",
            "ETA_VOL_A": vol["theta"]["a"],
            "ETA_VOL_B": vol["theta"].get("b", 0.0) if vol_key != "V6" else 0.0,
            "ETA_VOL_C": vol["theta"].get("c", 0.0)
            if vol_key == "V7"
            else (vol["theta"]["b"] if vol_key == "V6" else 0.0),
            "floor": 0.50,
            "fit_wrmse": vol["wrmse"],
            "fit_wmape_pct": vol["wmape_pct"],
            "loco_wmape_pct": float(mv.loco_wmape_pct),
            "st_wmape_pct": float(mv.st_wmape_pct),
            "legacy_loco_wmape_pct": selv["legacy_loco_wmape_pct"],
            "automatic_selection": selv["family"],
        },
        "eta_oi": {
            "family": oi_key,
            "formula": "eta_oi = (ETA_OI_A - ETA_OI_B*PR - ETA_OI_C/PR) * s(n*) * (1 - ETA_LEAK_C*(PR-1)*u_L) * h(n*)",
            "ETA_OI_A": oi["theta_g"]["A"],
            "ETA_OI_B": oi["theta_g"]["B"],
            "ETA_OI_C": oi["theta_g"]["C"],
            "ETA_LEAK_C": c,
            "ETA_LEAK_TWO_SIDED": leak_two_sided,
            "ETA_FLOW_D": d,
            "ETA_FLOW_TWO_SIDED": flow_two_sided,
            "ETA_OI_HAS_DRIVE": s_key != "E0",
            "N_STAR_MAX": 2.0,
            "fit_wrmse": oi["wrmse"],
            "fit_wmape_pct": oi["wmape_pct"],
            "loco_wmape_pct": float(mo.loco_wmape_pct),
            "st_wmape_pct": float(mo.st_wmape_pct),
            "legacy_loco_wmape_pct": selo["legacy_loco_wmape_pct"],
            "automatic_selection": selo["family"],
            "tie_break": selo.get("tie_break", ""),
            "chain_accepted": selo.get("chain_accepted", []),
            "rejected_by_R4_within_machine_identification": selo.get("rejected_by_R4", []),
            "R4_ratios": selo.get("R4", {}),
        },
        "split": {
            "ETA_EM_REF": split["ETA_EM_REF"],
            "ETA_EM_N0": n0,
            "ETA_EM_N0_anchor": anchor,
            "ETA_EM_N0_total_cuevas": split["ETA_EM_N0"],
            "ETA_EM_N0_drive_ossorio": split["ossorio_drive_only"]["n0_drive_median"],
            "ETA_EM_P0": split["ETA_EM_P0"],
            "basis": split["source"],
            "selected_model": split["selected"],
            "n_rows": split["n_rows"],
            "models": split["models"],
            "ossorio_drive_only": split.get("ossorio_drive_only", {}),
            "eta_em_formula": "eta_em = ETA_EM_REF * n*(1+ETA_EM_N0)/(n*+ETA_EM_N0) * m(PR; ETA_EM_P0)",
            "eta_isen_formula": "eta_isen = max(ETA_ISEN_FLOOR, eta_oi / eta_em)",
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
#: lives in ``validation/coefficients/{c["version"]}/`` ({c["estimator"]} estimator).
COEFFICIENT_VERSION = "{c["version"]}"
#: Volumetric efficiency ``1 - A (PR-1) - B u - C (PR-1) u``, ``u = max(0, 1/n* - 1)`` --
#: family {v["family"]}, {c["machines"]} machines, {c["speed_records"]} speed records,
#: LOCO MAPE {v["loco_wmape_pct"]:.2f} % (legacy {v["legacy_loco_wmape_pct"]:.2f} %), speed transfer {v["st_wmape_pct"]:.2f} %.
ETA_VOL_A = {v["ETA_VOL_A"]:.5f}
ETA_VOL_B = {v["ETA_VOL_B"]:.5f}
ETA_VOL_C = {v["ETA_VOL_C"]:.5f}
#: Lift shape of the electrical-to-isentropic product ``A - B PR - C/PR`` --
#: family {o["family"]}, LOCO MAPE {o["loco_wmape_pct"]:.2f} % (legacy {o["legacy_loco_wmape_pct"]:.2f} %), speed transfer {o["st_wmape_pct"]:.2f} %.
ETA_OI_A = {o["ETA_OI_A"]:.5f}
ETA_OI_B = {o["ETA_OI_B"]:.5f}
ETA_OI_C = {o["ETA_OI_C"]:.5f}
#: Leakage interaction ``1 - c (PR-1) u_L``; two-sided means ``u_L = 1/n* - 1``.
ETA_LEAK_C = {o["ETA_LEAK_C"]:.5f}
ETA_LEAK_TWO_SIDED = {o["ETA_LEAK_TWO_SIDED"]}
#: Flow-loss speed factor ``1 - d (n*^2 - 1)`` (two-sided) or ``1 - d max(0, n*-1)^2``.
ETA_FLOW_D = {o["ETA_FLOW_D"]:.5f}
ETA_FLOW_TWO_SIDED = {o["ETA_FLOW_TWO_SIDED"]}
#: Whether the fitted product carries the drive factor (family {o["family"]}).
ETA_OI_HAS_DRIVE = {o["ETA_OI_HAS_DRIVE"]}
#: Drive + motor: ``s(n*) = n*(1+n0)/(n*+n0)`` with ``n0`` from the {s["ETA_EM_N0_anchor"]} anchor
#: (total, Cuevas & Lebrun 2009: {s["ETA_EM_N0_total_cuevas"]:.4f}; drive-only floor, Ossorio &
#: Navarro-Peris 2023: {s["ETA_EM_N0_drive_ossorio"]:.4f}); motor load ``m(PR)`` with ``p0``;
#: level at PR 3, n* 1 (Cuevas & Lebrun 2009 discharge-temperature split, {s["n_rows"]} rows).
ETA_EM_N0 = {s["ETA_EM_N0"]:.5f}
ETA_EM_P0 = {s["ETA_EM_P0"]:.5f}
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
    ap.add_argument("--mode", default=None, choices=("pooled", "fe"))
    ap.add_argument("--vol", default=None, help="override the volumetric family (V2/V6/V7)")
    ap.add_argument("--oi", default=None, help="override the product family key, e.g. I2xF2xL1")
    ap.add_argument("--em-anchor", default="drive", choices=("drive", "total"), help="n0 of eta_em for E0 products")
    ap.add_argument("--note", default="", help="decision note recorded in coefficients.json")
    ap.add_argument("--no-module", action="store_true", help="archive only; do not touch the library module")
    a = ap.parse_args()
    c = build_coefficients(a.version, a.mode, a.vol, a.oi, a.em_anchor, a.note)
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
        "estimator": c["estimator"],
        "selection_rule": "sequential nesting; +1 coefficient must buy >= 0.1 pp in the metric its term targets "
        "(speed terms: leave-one-compressor-out speed-transfer MAPE, lift terms: LOCO MAPE) and cost <= 0.1 pp in the other; "
        "no stratum with >= 3 machines worse by > 2 pp or > 25 %; constraints 0 < eta <= 1.02 and monotone delivered duty on "
        "PR 1.5-8, n* 0.15-2.5; a speed or lift x speed term must be seen within the machines that identify it (median "
        "within-machine/added ratio >= 2/3, >= 3 machines from >= 2 sources); ties within 0.1 pp go to the form with a physical "
        "precedent; the drive factor is anchored on the discharge-temperature split (Cuevas & Lebrun 2009) and bounded below by "
        "the drive-only measurements of Ossorio & Navarro-Peris 2023",
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
    for name in (
        "loco_pooled.csv",
        "loco_strata.csv",
        "model_selection.csv",
        "fit_results.json",
        "split.json",
        "selected.json",
        "within_machine_contrasts.csv",
    ):
        if (DATA_DIR / name).exists():
            shutil.copy(DATA_DIR / name, out / name)
    if not a.no_module:
        rewrite_module(render_block(c))
        print(f"module block regenerated for {a.version}")
    print(f"archive -> {out}")


if __name__ == "__main__":
    main()
