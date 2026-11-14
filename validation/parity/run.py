"""Run every catalogue through TMHP's default rules and score the result.

    uv run python -m validation.parity.run                 # all catalogues
    uv run python -m validation.parity.run --unit <slug>   # one
    scripts/regen-validation.sh                            # all, in parallel, plus site data

Writes ``validation/results/<slug>.csv`` per unit and rewrites
``validation/results/summary.csv`` and ``manifest.json``. Those files are what
the documentation site reads, so adding a catalogue and rerunning this is the
whole update procedure.

Provenance and parallel runs
----------------------------
Every run writes, next to each unit CSV, ``provenance/<slug>.json``: the commit,
whether the model and harness had uncommitted edits (and a digest of them), the
``src/tmhp`` and ``validation/parity`` tree hashes, the coefficient version,
the Python, CoolProp and platform, and when the unit ran.

``summary.csv`` and ``manifest.json`` are always built from the files on disk
(:func:`summarize`), whichever path wrote them. The summary refuses a CSV with
no provenance record and refuses units whose records disagree on the code, the
harness, the coefficients or the environment (:data:`MUST_AGREE`), naming the
units that differ. The manifest carries the provenance the units share -- never
that of the process doing the summarising -- and is dirty if any unit was.

``--unit <slug> --no-summary`` writes one unit's CSV and record without touching
the summary, so units can run in separate processes; ``--summarize-only`` then
builds the summary. ``scripts/regen-validation.sh`` does both.

What is held fixed
------------------
Everything. The model is constructed from the nameplate capacity, the
refrigerant, and whatever the manufacturer publishes about the machine. No
efficiency coefficient, conductance or approach temperature is touched. That
is the point: the derivation work in ``validation/extraction/`` is only worth
anything if the numbers it produces are then used without adjustment.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import subprocess
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import COEFFICIENT_VERSION

from .spec import Catalog, OperatingPoint, load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "validation" / "results"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"


def _build_model(catalog: Catalog, overrides: dict | None = None, mode: str = "cooling"):
    """Build the model for a catalogue.

    ``overrides`` exists for the coefficient ablation in
    :mod:`validation.analysis.en14825_trend` and is empty on the shipped path,
    so the published numbers are still produced with nothing touched.
    ``mode`` selects the published indoor air flow when a manual prints a
    different one for heating (``rated_indoor_air_flow_heating_m3_s``).
    """
    kwargs: dict = {
        "ref": catalog.refrigerant,
        "hp_capacity": catalog.nominal_capacity_kW * 1000.0,
    }
    published = catalog.published_inputs
    if published.get("displacement_cc"):
        kwargs["V_cmp_ref"] = float(published["displacement_cc"]) * 1.0e-6

    if catalog.model_class == "ASHPB":
        if published.get("rated_air_flow_m3_s"):
            kwargs["dV_fan_a_rated"] = float(published["rated_air_flow_m3_s"])
        kwargs.update(overrides or {})
        return AirSourceHeatPumpBoiler(**kwargs)

    # Widen the approach-temperature *search domain* for every air-to-air unit.
    #
    # This is the numerical box the operating-point search may look in, not a
    # component limit and not a fitted quantity: the pressure-ratio envelope and
    # ``dT_hx_min`` are what keep a solution physical. The model default is
    # 20 K, which is right for :meth:`max_capacity`, but it cuts off catalogue
    # rows at the top of the range -- a split unit asked for its maximum output
    # runs a wide approach on the indoor coil. Measured across the adopted
    # air-to-air set, 20 K leaves 38 published rows with no solution at all,
    # almost all of them on the two largest units (RXM42A 70 -> 56, RXM50A
    # 71 -> 46). Dropping rows because the search box was too small is not a
    # result; it is a missing measurement.
    #
    # It is applied uniformly to every catalogue, before any result is seen, and
    # it admits solutions rather than moving them: a row that solves under both
    # settings returns the same COP. ``ALLOWED_PUBLISHED_INPUTS`` is untouched --
    # this is a harness setting, not a manufacturer specification, and it is
    # deliberately not per-unit.
    kwargs["dT_approach_bounds"] = (1.0, 30.0)
    if published.get("rated_air_flow_m3_s"):
        kwargs["dV_ou_fan_a_rated"] = float(published["rated_air_flow_m3_s"])
    indoor_key = "rated_indoor_air_flow_m3_s"
    if mode == "heating" and published.get("rated_indoor_air_flow_heating_m3_s"):
        indoor_key = "rated_indoor_air_flow_heating_m3_s"
    if published.get(indoor_key):
        kwargs["dV_iu_fan_a_rated"] = float(published[indoor_key])
    kwargs.update(overrides or {})
    return AirSourceHeatPump(**kwargs)


def _run_point(model, catalog: Catalog, point: OperatingPoint) -> dict:
    if catalog.model_class == "ASHPB":
        result = model.analyze_steady(
            T_tank_w=point.t_sink_C - catalog.sink_offset_K,
            T0=point.t_source_C,
            Q_ref_tank=point.q_kW * 1000.0,
            return_dict=True,
        )
    else:
        # Air-to-air sign convention: positive load is cooling.
        load_w = point.q_kW * 1000.0
        if point.mode == "heating":
            load_w = -load_w
        result = model.analyze_steady(
            Q_r_iu=load_w,
            T0=point.t_source_C,
            T_a_room=point.t_sink_C,
            return_dict=True,
            verbose=False,
        )
    assert isinstance(result, dict)
    return result


#: Scoring reason: the model delivered less than the published duty.
CAPACITY_SHORT = "capacity_short"
#: Shortfall above which a point is not scored (fraction of the published duty).
CAPACITY_SHORT_TOL = 0.01


def _capacity_short(catalog: Catalog, point: OperatingPoint, result: dict) -> bool:
    """Whether the delivered duty falls more than 1 % short of the published one."""
    key = "Q_ref_tank [W]" if catalog.model_class == "ASHPB" else "Q_ref_iu [W]"
    try:
        delivered = abs(float(result[key]))
    except (KeyError, TypeError, ValueError):
        return False
    requested = point.q_kW * 1000.0
    return math.isfinite(delivered) and delivered < requested * (1.0 - CAPACITY_SHORT_TOL)


def run_catalog(catalog: Catalog, overrides: dict | None = None) -> pd.DataFrame:
    models = {"cooling": _build_model(catalog, overrides, "cooling")}
    if catalog.published_inputs.get("rated_indoor_air_flow_heating_m3_s"):
        models["heating"] = _build_model(catalog, overrides, "heating")
    rows = []
    for point in catalog.points:
        model = models.get(point.mode, models["cooling"])
        result = _run_point(model, catalog, point)
        failure = result.get("failure_reason", "none")
        if failure == "none" and _capacity_short(catalog, point, result):
            # The model ran but could not deliver the published duty (a
            # capacity ceiling): its COP belongs to a different operating
            # point than the catalogue's, so the point is not scored.
            failure = CAPACITY_SHORT
        cop_pred = float(result.get("cop_sys [-]", float("nan")))
        cop_target = point.target_cop()
        usable = failure == "none" and math.isfinite(cop_pred) and cop_pred > 0.0
        rows.append(
            {
                "slug": catalog.slug,
                "unit": catalog.name,
                "manufacturer": catalog.manufacturer,
                "model_class": catalog.model_class,
                "refrigerant": catalog.refrigerant,
                "nominal_kW": catalog.nominal_capacity_kW,
                "rating_standard": catalog.rating_standard,
                "status": catalog.status,
                "coefficient_version": COEFFICIENT_VERSION,
                "point_id": point.id,
                "mode": point.mode,
                "t_source_C": point.t_source_C,
                "t_sink_C": point.t_sink_C,
                "q_kW": point.q_kW,
                "cop_target": cop_target,
                "cop_pred": cop_pred if usable else float("nan"),
                "cop_basis": "final_indoor_air_sensible" if catalog.model_class == "ASHP" else "tank_duty",
                "abs_error": abs(cop_pred - cop_target) if usable else float("nan"),
                "abs_pct_error": abs(cop_pred - cop_target) / cop_target * 100.0 if usable else float("nan"),
                "power_target_kW": point.q_kW / cop_target,
                # Requested catalogue duty is a coil input, not the final-air
                # COP numerator; read the actual electrical input directly.
                "power_pred_kW": float(result["E_tot [W]"]) / 1000 if usable else float("nan"),
                "rps": (result.get("cmp_rpm [rpm]", float("nan")) or float("nan")) / 60.0,
                "n_star": result.get("n_star [-]", float("nan")),
                "pr_cmp": result.get("pr_cmp [-]", float("nan")),
                "eta_cmp_vol": result.get("eta_cmp_vol [-]", float("nan")),
                "eta_cmp_isen": result.get("eta_cmp_isen [-]", float("nan")),
                "eta_cmp": result.get("eta_cmp [-]", float("nan")),
                "capacity_clamped": result.get("capacity_clamped"),
                "pr_floor_active": result.get("pr_floor_active"),
                "failure_reason": failure,
                "usable": usable,
            }
        )
    return pd.DataFrame(rows)


def _summarise(df: pd.DataFrame) -> dict:
    good = df[df.usable]
    power_ape = (
        (good.power_pred_kW - good.power_target_kW).abs() / good.power_target_kW * 100.0
        if len(good)
        else pd.Series(dtype=float)
    )
    return {
        "slug": df.slug.iat[0],
        "unit": df.unit.iat[0],
        "manufacturer": df.manufacturer.iat[0],
        "model_class": df.model_class.iat[0],
        "refrigerant": df.refrigerant.iat[0],
        "nominal_kW": df.nominal_kW.iat[0],
        "rating_standard": df.rating_standard.iat[0],
        "status": df.status.iat[0],
        "coefficient_version": COEFFICIENT_VERSION,
        "points": len(df),
        "points_usable": int(len(good)),
        "cop_MAE": float(good.abs_error.mean()) if len(good) else float("nan"),
        "cop_MAPE_pct": float(good.abs_pct_error.mean()) if len(good) else float("nan"),
        "cop_max_APE_pct": float(good.abs_pct_error.max()) if len(good) else float("nan"),
        "power_MAPE_pct": float(power_ape.mean()) if len(good) else float("nan"),
        "cop_bias_pct": float(((good.cop_pred - good.cop_target) / good.cop_target * 100.0).mean())
        if len(good)
        else float("nan"),
    }


#: Paths whose contents determine the per-unit results. A run is ``dirty`` when
#: any of them differs from ``git_head``.
SOURCE_PATHS = ("src/tmhp", "validation/parity", "validation/catalogs", "pyproject.toml", "uv.lock")

#: Where each unit run records what produced its CSV: ``provenance/<slug>.json``.
PROVENANCE_DIR_NAME = "provenance"

#: Provenance fields every unit must share before their CSVs may be summarised
#: together: the model code, the harness, the coefficients, the local edits (if
#: any) and the numerical environment. ``git_head`` may differ (a later commit
#: that touched none of these produces the same numbers), and ``catalog_blob``
#: and ``run_at`` are per unit by nature.
MUST_AGREE = (
    "coefficient_version",
    "src_tmhp_tree",
    "validation_parity_tree",
    "dirty_digest",
    "python",
    "coolprop",
    "platform",
)


def _provenance_path(results_dir: Path, slug: str) -> Path:
    return results_dir / PROVENANCE_DIR_NAME / f"{slug}.json"


def _git(*argv: str) -> str:
    try:
        return subprocess.run(
            ["git", *argv], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _dirty_digest() -> str | None:
    """sha256 of the uncommitted changes under :data:`SOURCE_PATHS`, or None when clean.

    Two dirty runs on the same ``git_head`` ran the same code only if their
    edits were the same; the digest makes that comparable.
    """
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary", "--", *SOURCE_PATHS], cwd=REPO_ROOT, capture_output=True, check=False
    ).stdout
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATHS).splitlines()
    if not diff and not untracked:
        return None
    digest = hashlib.sha256(diff)
    for name in sorted(untracked):
        digest.update(name.encode())
        digest.update((REPO_ROOT / name).read_bytes())
    return digest.hexdigest()


def source_state() -> dict:
    """What this process computes with: code, harness, coefficients, environment.

    Captured once per run, before any catalogue is evaluated, and written next
    to every unit CSV the run produces (:func:`write_unit`). ``src_tmhp_tree``
    and ``validation_parity_tree`` are tree hashes at ``git_head``; when
    ``dirty`` is true they do not describe the code alone, and ``dirty_digest``
    identifies the uncommitted edits.
    """
    import CoolProp

    digest = _dirty_digest()
    return {
        "coefficient_version": COEFFICIENT_VERSION,
        "git_head": _git("rev-parse", "HEAD"),
        "dirty": digest is not None,
        "dirty_digest": digest,
        "dirty_scope": list(SOURCE_PATHS),
        "src_tmhp_tree": _git("rev-parse", "HEAD:src/tmhp"),
        "validation_parity_tree": _git("rev-parse", "HEAD:validation/parity"),
        "python": platform.python_version(),
        "coolprop": CoolProp.__version__,
        "platform": f"{platform.system()}-{platform.machine()}",
    }


def _replace_atomically(path: Path, write) -> None:
    """``write(tmp)`` then rename over ``path``: a reader sees the old file or the new one, never half."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_unit(results_dir: Path, catalog: Catalog, df: pd.DataFrame, state: dict) -> None:
    """Write one unit's CSV and the provenance of the run that produced it.

    The order makes an interrupted write fail closed. The old record is removed
    before the CSV is touched, and the new record is written only after the new
    CSV is in place, so an old record can never vouch for a new CSV: a crash in
    between leaves a CSV with no record, which :func:`summarize` refuses.
    """
    record_path = _provenance_path(results_dir, catalog.slug)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.unlink(missing_ok=True)
    _replace_atomically(results_dir / f"{catalog.slug}.csv", lambda tmp: df.to_csv(tmp, index=False))
    record = {
        "slug": catalog.slug,
        **state,
        "catalog_blob": _git("rev-parse", f"HEAD:validation/catalogs/{catalog.slug}.yaml"),
        "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    _replace_atomically(record_path, lambda tmp: tmp.write_text(json.dumps(record, indent=1) + "\n"))


def _read_unit_results(catalogs: list[Catalog], results_dir: Path) -> tuple[list[pd.DataFrame], list[dict]]:
    """Per-unit CSVs and their provenance records, in catalogue order.

    Refuses a CSV with no provenance record, files without a catalogue, and a
    CSV whose coefficient column disagrees with its record.
    """
    registered = {c.slug for c in catalogs}
    missing = [c.slug for c in catalogs if not (results_dir / f"{c.slug}.csv").exists()]
    if missing:
        raise SystemExit(f"summary: no results for {', '.join(missing)} under {results_dir}")
    unrecorded = [c.slug for c in catalogs if not _provenance_path(results_dir, c.slug).exists()]
    if unrecorded:
        raise SystemExit(
            f"summary: no provenance record for {', '.join(unrecorded)} "
            f"({PROVENANCE_DIR_NAME}/<slug>.json) -- these CSVs predate provenance "
            "recording or were not written by this harness; rerun them"
        )
    orphans = sorted(
        [p.name for p in results_dir.glob("*.csv") if p.name != SUMMARY_CSV.name and p.stem not in registered]
        + [
            f"{PROVENANCE_DIR_NAME}/{p.name}"
            for p in (results_dir / PROVENANCE_DIR_NAME).glob("*.json")
            if p.stem not in registered
        ]
    )
    if orphans:
        raise SystemExit(f"summary: results without a catalogue: {', '.join(orphans)} -- remove them")

    frames, records = [], []
    for catalog in catalogs:
        # round_trip: the default parser can be one ulp off, and the summary must
        # be bit-identical whichever path wrote the CSVs.
        df = pd.read_csv(results_dir / f"{catalog.slug}.csv", float_precision="round_trip")
        record = json.loads(_provenance_path(results_dir, catalog.slug).read_text())
        versions = set(df.coefficient_version.astype(str))
        if versions != {record.get("coefficient_version")}:
            raise SystemExit(
                f"summary: {catalog.slug}.csv says coefficients {sorted(versions)}, "
                f"its provenance record says {record.get('coefficient_version')!r} -- rerun that unit"
            )
        frames.append(df)
        records.append(record)
    return frames, records


def common_provenance(records: list[dict]) -> dict:
    """The provenance every unit shares, or SystemExit naming the units that differ."""
    disagreements = []
    for key in MUST_AGREE:
        values: dict[str, list[str]] = {}
        for record in records:
            values.setdefault(json.dumps(record.get(key)), []).append(record["slug"])
        if len(values) > 1:
            # Name the minority: the units that differ from what most units say.
            majority = max(values, key=lambda v: len(values[v]))
            odd = ", ".join(
                f"{slug}={json.loads(v)!r}" for v, slugs in values.items() if v != majority for slug in slugs
            )
            disagreements.append(f"{key}: most units {json.loads(majority)!r}; differ: {odd}")
    if disagreements:
        raise SystemExit(
            "summary: the unit results were not produced by the same code and environment\n  "
            + "\n  ".join(disagreements)
            + "\nrerun the differing units (or all: scripts/regen-validation.sh)"
        )
    first = records[0]
    heads = sorted({r["git_head"] for r in records})
    times = sorted(r["run_at"] for r in records)
    return {
        **{key: first[key] for key in MUST_AGREE},
        "git_head": heads[0] if len(heads) == 1 else None,
        "git_heads": heads,
        "dirty": any(r["dirty"] for r in records),
        "dirty_scope": first["dirty_scope"],
        "run_started": times[0],
        "run_finished": times[-1],
    }


def summarize(catalogs: list[Catalog], results_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Build ``summary.csv`` and ``manifest.json`` from the per-unit files on disk.

    Every path ends here -- the single-process run, ``--unit`` and
    ``--summarize-only`` -- so they write the same files for the same unit
    results. The manifest carries the provenance the unit runs recorded, never
    that of the process doing the summarising.
    """
    frames, records = _read_unit_results(catalogs, results_dir)
    provenance = common_provenance(records)
    summary = pd.DataFrame([_summarise(df) for df in frames])
    summary = summary.sort_values(["model_class", "manufacturer", "nominal_kW"]).reset_index(drop=True)
    adopted = summary[summary.status == "adopted"]
    held = summary[summary.status == "hold"]
    manifest = {
        **provenance,
        "provenance": f"{PROVENANCE_DIR_NAME}/<slug>.json, one per unit",
        "space_conditioning_cop_basis": "final_indoor_air_sensible",
        "units": int(len(summary)),
        "adopted_units": int(len(adopted)),
        "held_units": sorted(held.slug.tolist()),
    }
    summary.to_csv(results_dir / SUMMARY_CSV.name, index=False)
    (results_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return summary, manifest


def _print_summary(summary: pd.DataFrame) -> None:
    print(f"Catalogue parity -- library defaults applied unchanged (coefficients {COEFFICIENT_VERSION})")
    print()
    cols = [
        "unit",
        "refrigerant",
        "rating_standard",
        "status",
        "points",
        "points_usable",
        "cop_MAE",
        "cop_MAPE_pct",
        "cop_bias_pct",
    ]
    print(summary[cols].to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print()
    # One headline per (model class, rating standard); held catalogues are diagnostics and
    # never pooled into a headline -- different test standards define different COPs.
    adopted = summary[summary.status == "adopted"]
    for (model_class, standard), grp in adopted.groupby(["model_class", "rating_standard"]):
        usable = grp.points_usable.sum()
        weighted = (grp.cop_MAPE_pct * grp.points_usable).sum() / max(usable, 1)
        bias = (grp.cop_bias_pct * grp.points_usable).sum() / max(usable, 1)
        print(
            f"  {model_class:5s} {standard:13s}: {len(grp)} units, {usable}/{grp.points.sum()} points, point-weighted COP MAPE {weighted:.1f} %, bias {bias:+.1f} %"
        )
    held = summary[summary.status == "hold"]
    if len(held):
        print(f"  held (diagnostic only, not in any headline): {', '.join(held.unit)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unit", default=None, help="run one catalogue by slug")
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="write only the unit CSV and its provenance record; leave summary.csv and manifest.json alone",
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="run nothing: rebuild summary.csv and manifest.json from the per-unit CSVs and provenance records",
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where to read and write results")
    args = parser.parse_args(argv)
    if args.summarize_only and (args.unit or args.no_summary):
        parser.error("--summarize-only takes no --unit or --no-summary")

    results_dir: Path = args.results_dir.resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    if not args.summarize_only:
        to_run = load_all(args.unit)
        if not to_run:
            raise SystemExit("no catalogues found under validation/catalogs/")
        state = source_state()
        for catalog in to_run:
            write_unit(results_dir, catalog, run_catalog(catalog), state)
        if args.no_summary:
            print(
                f"  wrote {len(to_run)} unit CSV(s) and provenance record(s); summary.csv and manifest.json untouched"
            )
            return

    catalogs = load_all()
    if not catalogs:
        raise SystemExit("no catalogues found under validation/catalogs/")
    summary, manifest = summarize(catalogs, results_dir)
    _print_summary(summary)
    flag = " (DIRTY)" if manifest["dirty"] else ""
    print(f"  wrote {results_dir / SUMMARY_CSV.name} and manifest.json{flag}")


if __name__ == "__main__":
    main()
