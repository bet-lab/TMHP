"""Run every catalogue through TMHP's default rules and score the result.

    uv run python -m validation.parity.run                 # all catalogues
    uv run python -m validation.parity.run --unit <slug>   # one

Writes ``validation/results/<slug>.csv`` per unit and rewrites
``validation/results/summary.csv``. Those files are what the documentation
site reads, so adding a catalogue and rerunning this is the whole update
procedure.

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
import math
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler

from .spec import Catalog, OperatingPoint, load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "validation" / "results"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"


def _build_model(catalog: Catalog, overrides: dict | None = None):
    """Build the model for a catalogue.

    ``overrides`` exists for the coefficient ablation in
    :mod:`validation.analysis.en14825_trend` and is empty on the shipped path,
    so the published numbers are still produced with nothing touched.
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

    if published.get("rated_air_flow_m3_s"):
        kwargs["dV_ou_fan_a_rated"] = float(published["rated_air_flow_m3_s"])
    if published.get("rated_indoor_air_flow_m3_s"):
        kwargs["dV_iu_fan_a_rated"] = float(published["rated_indoor_air_flow_m3_s"])
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
    model = _build_model(catalog, overrides)
    rows = []
    for point in catalog.points:
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
                "point_id": point.id,
                "mode": point.mode,
                "t_source_C": point.t_source_C,
                "t_sink_C": point.t_sink_C,
                "q_kW": point.q_kW,
                "cop_target": cop_target,
                "cop_pred": cop_pred if usable else float("nan"),
                "abs_error": abs(cop_pred - cop_target) if usable else float("nan"),
                "abs_pct_error": abs(cop_pred - cop_target) / cop_target * 100.0 if usable else float("nan"),
                "power_target_kW": point.q_kW / cop_target,
                "power_pred_kW": (point.q_kW / cop_pred) if usable else float("nan"),
                "rps": (result.get("cmp_rpm [rpm]", float("nan")) or float("nan")) / 60.0,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", default=None, help="run one catalogue by slug")
    args = parser.parse_args()

    catalogs = load_all(args.unit)
    if not catalogs:
        raise SystemExit("no catalogues found under validation/catalogs/")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    for catalog in catalogs:
        df = run_catalog(catalog)
        df.to_csv(RESULTS_DIR / f"{catalog.slug}.csv", index=False)
        summaries.append(_summarise(df))

    summary = pd.DataFrame(summaries)
    if args.unit and SUMMARY_CSV.exists():
        previous = pd.read_csv(SUMMARY_CSV)
        summary = pd.concat([previous[previous.slug != args.unit], summary], ignore_index=True)
    summary = summary.sort_values(["model_class", "manufacturer", "nominal_kW"]).reset_index(drop=True)
    summary.to_csv(SUMMARY_CSV, index=False)

    print("Catalogue parity -- library defaults applied unchanged")
    print()
    cols = ["unit", "refrigerant", "nominal_kW", "points", "points_usable", "cop_MAE", "cop_MAPE_pct", "cop_bias_pct"]
    print(summary[cols].to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print()
    usable = summary.points_usable.sum()
    total = summary.points.sum()
    weighted = (summary.cop_MAPE_pct * summary.points_usable).sum() / max(usable, 1)
    print(f"  {len(summary)} units, {usable}/{total} points evaluated, point-weighted COP MAPE {weighted:.1f} %")
    print(f"  wrote {SUMMARY_CSV.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
