"""Run every catalogue through TMHP's default rules and score the result.

    uv run python -m validation.parity.run                 # all catalogues
    uv run python -m validation.parity.run --unit <slug>   # one
    scripts/regen-validation.sh                            # all, in parallel, plus site data

Writes ``validation/results/<slug>.csv`` per unit and rewrites
``validation/results/summary.csv`` and ``manifest.json``. Those files are what
the documentation site reads, so adding a catalogue and rerunning this is the
whole update procedure.

Parallel runs
-------------
``--unit <slug> --no-summary`` writes only that unit's CSV, so several units
can run in separate processes without racing on ``summary.csv``.
``--summarize-only`` then reads the per-unit CSVs of every registered catalogue
and writes ``summary.csv`` and ``manifest.json`` through the same
:func:`_summarise` the full run uses. ``scripts/regen-validation.sh`` does both.

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
import json
import math
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


def run_catalog(catalog: Catalog, overrides: dict | None = None) -> pd.DataFrame:
    models = {"cooling": _build_model(catalog, overrides, "cooling")}
    if catalog.published_inputs.get("rated_indoor_air_flow_heating_m3_s"):
        models["heating"] = _build_model(catalog, overrides, "heating")
    rows = []
    for point in catalog.points:
        model = models.get(point.mode, models["cooling"])
        result = _run_point(model, catalog, point)
        failure = result.get("failure_reason", "none")
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
                "pr_clamped": result.get("pr_clamped"),
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


#: Paths whose contents determine the per-unit results. ``manifest.json``
#: records whether any of them differed from ``git_head`` when it was written.
SOURCE_PATHS = ("src/tmhp", "validation/parity", "validation/catalogs", "pyproject.toml", "uv.lock")


def _read_unit_results(catalogs: list[Catalog], results_dir: Path) -> list[pd.DataFrame]:
    """Per-unit CSVs written by earlier ``--unit`` runs, in catalogue order."""
    missing = [c.slug for c in catalogs if not (results_dir / f"{c.slug}.csv").exists()]
    if missing:
        raise SystemExit(f"--summarize-only: no results for {', '.join(missing)} under {results_dir}")
    registered = {c.slug for c in catalogs}
    orphans = sorted(
        p.name for p in results_dir.glob("*.csv") if p.name != SUMMARY_CSV.name and p.stem not in registered
    )
    if orphans:
        raise SystemExit(f"--summarize-only: results without a catalogue: {', '.join(orphans)} -- remove them")
    frames = []
    for catalog in catalogs:
        # round_trip: the default parser can be one ulp off, and the summary must
        # be bit-identical to the one a single-process run builds in memory.
        df = pd.read_csv(results_dir / f"{catalog.slug}.csv", float_precision="round_trip")
        versions = set(df.coefficient_version.astype(str))
        if versions != {COEFFICIENT_VERSION}:
            raise SystemExit(
                f"--summarize-only: {catalog.slug}.csv was written with coefficients {sorted(versions)}, "
                f"the library is {COEFFICIENT_VERSION} -- rerun that unit"
            )
        frames.append(df)
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unit", default=None, help="run one catalogue by slug")
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="write only the per-unit CSV; leave summary.csv and manifest.json alone",
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="run nothing: rebuild summary.csv and manifest.json from the per-unit CSVs of every catalogue",
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where to read and write results")
    args = parser.parse_args()
    if args.summarize_only and (args.unit or args.no_summary):
        parser.error("--summarize-only takes no --unit or --no-summary")

    results_dir: Path = args.results_dir.resolve()
    summary_csv = results_dir / SUMMARY_CSV.name

    catalogs = load_all(None if args.summarize_only else args.unit)
    if not catalogs:
        raise SystemExit("no catalogues found under validation/catalogs/")

    results_dir.mkdir(parents=True, exist_ok=True)
    if args.summarize_only:
        summaries = [_summarise(df) for df in _read_unit_results(catalogs, results_dir)]
    else:
        summaries = []
        for catalog in catalogs:
            df = run_catalog(catalog)
            df.to_csv(results_dir / f"{catalog.slug}.csv", index=False)
            summaries.append(_summarise(df))
        if args.no_summary:
            print(f"  wrote {len(catalogs)} unit CSV(s); summary.csv and manifest.json untouched")
            return

    summary = pd.DataFrame(summaries)
    if args.unit and summary_csv.exists():
        previous = pd.read_csv(summary_csv, float_precision="round_trip")
        summary = pd.concat([previous[previous.slug != args.unit], summary], ignore_index=True)
    summary = summary.sort_values(["model_class", "manufacturer", "nominal_kW"]).reset_index(drop=True)
    summary.to_csv(summary_csv, index=False)

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
    manifest = {
        "coefficient_version": COEFFICIENT_VERSION,
        **_source_state(),
        "space_conditioning_cop_basis": "final_indoor_air_sensible",
        "units": int(len(summary)),
        "adopted_units": int(len(adopted)),
        "held_units": sorted(held.slug.tolist()),
    }
    (results_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"  wrote {summary_csv} and manifest.json")


def _git(*argv: str) -> str:
    try:
        return subprocess.run(
            ["git", *argv], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _source_state() -> dict:
    """What the results were computed from, as of writing the manifest.

    ``dirty`` is true when any of :data:`SOURCE_PATHS` differs from ``git_head``
    (staged, unstaged or untracked), i.e. when ``git_head`` alone does not
    reproduce the numbers. ``src_tmhp_tree`` is the tree hash of ``src/tmhp`` at
    ``git_head``: two clean runs with the same tree ran the same model code.
    """
    import CoolProp

    status = _git("status", "--porcelain", "--untracked-files=all", "--", *SOURCE_PATHS)
    return {
        "git_head": _git("rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirty_scope": list(SOURCE_PATHS),
        "src_tmhp_tree": _git("rev-parse", "HEAD:src/tmhp"),
        "run_date": datetime.date.today().isoformat(),
        "python": platform.python_version(),
        "coolprop": CoolProp.__version__,
    }


if __name__ == "__main__":
    main()
