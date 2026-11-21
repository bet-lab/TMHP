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
from tmhp.compressor_envelope import CONTROL_KEY, QUALITY_KEY, SUPERHEAT_KEY
from tmhp.standard_conditions import en14511_condenser_water, en14511_water_rise

from .spec import Catalog, OperatingPoint, load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "validation" / "results"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"
#: Harness overload for air-to-water units. This reaches more limits than a
#: 1.25x request and tests a smaller extrapolation than 2x (see issue #202).
BOILER_PROBE_MULTIPLIER = 1.5
#: Relative numerical tolerance used by the validation results reproduction test.
CAPACITY_PROBE_REL_TOL = 1e-6

CAPACITY_POINT_COLUMNS = (
    "q_pred_kW",
    "capacity_ratio",
    "capacity_binding",
    "x_suction",
    "superheat_K",
    "discharge_control",
    "t_discharge_C",
    "q_max_pred_kW",
    "capacity_headroom",
    "capacity_probe",
)


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
    # ``compressor_type`` and ``injection`` are reporting facts: the library has
    # one default efficiency map for every compressor (#217), and the
    # single-stage model has no injection.

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


def _run_point(model, catalog: Catalog, point: OperatingPoint, *, request_multiplier: float = 1.0) -> dict:
    """Solve at the catalogue boundary conditions, or mark a non-finite request unusable."""
    duty = point.q_kW * 1000.0
    if not math.isfinite(duty):
        return {"failure_reason": "invalid_catalogue_duty"}
    if catalog.model_class == "ASHPB":
        # EN 14511: the catalogue's leaving water, entered at the standard's rise below it, at the flow
        # that carries the published duty at that rise (tmhp.standard_conditions.en14511_condenser_water).
        if duty == 0.0:
            T_entering, m_dot_w = point.t_sink_C - en14511_water_rise(point.t_sink_C), 0.0
        else:
            T_entering, m_dot_w = en14511_condenser_water(point.t_sink_C, duty)
        result = model.analyze_steady(
            T_tank_w=T_entering,
            T0=point.t_source_C,
            Q_ref_tank=duty * request_multiplier,
            m_dot_w=m_dot_w,
            return_dict=True,
        )
    else:
        # Air-to-air sign convention: positive load is cooling.
        load_w = -duty if point.mode == "heating" else duty
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


def _result_number(result: dict, key: str) -> float:
    """Read a numeric result when the model reports one."""
    try:
        value = float(result[key])
    except (KeyError, TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def _capacity_ratio(duty_kW: float, published_kW: float) -> float:
    """Divide by the published duty when it is finite and nonzero."""
    return duty_kW / published_kW if math.isfinite(published_kW) and published_kW != 0.0 else math.nan


def _probe_capacity(
    model,
    catalog: Catalog,
    point: OperatingPoint,
    delivered_kW: float,
    *,
    multiplier: float = BOILER_PROBE_MULTIPLIER,
    rel_tol: float = CAPACITY_PROBE_REL_TOL,
) -> tuple[float, str | None]:
    """Return the probed duty in kW and label it as a ceiling or lower bound.

    Air-to-air uses the model's maximum-capacity solve. The boiler overload
    holds the catalogue water inlet temperature and flow and gives a lower
    bound when it meets the overload to the validation tolerance. Clamp a
    numerical inversion to the ordinary duty in both paths. A failed probe
    or a material drop gives a missing duty and label.
    """
    if not math.isfinite(point.q_kW):
        return math.nan, None
    kind = "ceiling"
    if catalog.model_class == "ASHP":
        ceiling = model.max_capacity(T0=point.t_source_C, T_a_room=point.t_sink_C, mode=point.mode)
        probed_kW = _result_number(ceiling, "Q_max [W]") / 1000.0
    else:
        if point.q_kW == 0.0:
            return math.nan, None
        overload = _run_point(model, catalog, point, request_multiplier=multiplier)
        if overload.get("failure_reason", "none") != "none":
            return math.nan, None
        probed_kW = _result_number(overload, "Q_ref_tank [W]") / 1000.0
        requested_kW = point.q_kW * multiplier
        if probed_kW >= requested_kW or math.isclose(probed_kW, requested_kW, rel_tol=rel_tol):
            kind = "lower_bound"
    if not math.isfinite(probed_kW):
        return math.nan, None
    if probed_kW < delivered_kW:
        if not math.isclose(probed_kW, delivered_kW, rel_tol=rel_tol):
            return math.nan, None
        probed_kW = delivered_kW
    return probed_kW, kind


#: Scoring reason: the model meets the published duty only as the mean of on/off cycling.
CYCLING_MEAN = "cycling_mean"


def _cycling_mean(result: dict) -> bool:
    """Whether the row is the mean over an on/off cycling period, not a running machine.

    A catalogue row is a unit running continuously at the published duty. Where
    the model's smallest continuous duty lies above that duty -- the minimum
    speed, or the duty below which the compressor envelope refuses lower
    speeds -- ``analyze_steady`` reports the request as met by cycling at that
    smallest duty (``capacity_clamped == "min"``): the delivered heat equals
    the published duty, but the COP carries the cycling degradation and
    belongs to the larger duty the machine runs at.
    """
    return result.get("capacity_clamped") == "min"


def run_catalog(catalog: Catalog, overrides: dict | None = None) -> pd.DataFrame:
    """Score COP at the published duty and then record independent probes of capacity."""
    models = {"cooling": _build_model(catalog, overrides, "cooling")}
    if catalog.published_inputs.get("rated_indoor_air_flow_heating_m3_s"):
        models["heating"] = _build_model(catalog, overrides, "heating")
    duty_key = "Q_ref_tank [W]" if catalog.model_class == "ASHPB" else "Q_ref_iu [W]"
    rows = []
    for point in catalog.points:
        model = models.get(point.mode, models["cooling"])
        result = _run_point(model, catalog, point)
        delivered = abs(_result_number(result, duty_key))
        requested = point.q_kW * 1000.0
        failure = result.get("failure_reason", "none")
        if failure == "none" and math.isfinite(delivered) and delivered < requested * (1.0 - CAPACITY_SHORT_TOL):
            # The model ran but could not deliver the published duty (a
            # capacity ceiling): its COP belongs to a different operating
            # point than the catalogue's, so the point is not scored.
            failure = CAPACITY_SHORT
        elif failure == "none" and _cycling_mean(result):
            # The model cannot run continuously at a duty this small: what it
            # reports is an on/off mean, a different quantity than the
            # catalogue's, so the point is not scored either.
            failure = CYCLING_MEAN
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
                "compressor_type": catalog.published_inputs.get("compressor_type", ""),
                "injection": catalog.published_inputs.get("injection", ""),
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
                "power_target_kW": point.q_kW / cop_target if cop_target != 0.0 else point.power_kW,
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
                "q_pred_kW": delivered / 1000.0,
                "capacity_ratio": _capacity_ratio(delivered / 1000.0, point.q_kW),
                "capacity_binding": result.get("capacity_binding"),
                "x_suction": _result_number(result, QUALITY_KEY),
                "superheat_K": _result_number(result, SUPERHEAT_KEY),
                "discharge_control": result.get(CONTROL_KEY),
                "t_discharge_C": _result_number(result, "T_ref_cmp_out [°C]"),
            }
        )
    # Complete all catalogue solves before requesting overloads so the extra
    # solves cannot affect the original scoring path.
    for point, row in zip(catalog.points, rows, strict=True):
        model = models.get(point.mode, models["cooling"])
        q_max, kind = _probe_capacity(model, catalog, point, row["q_pred_kW"])
        row.update(
            q_max_pred_kW=q_max,
            capacity_headroom=_capacity_ratio(q_max, point.q_kW),
            capacity_probe=kind,
        )
    return pd.DataFrame(rows)


def _capacity_score(df: pd.DataFrame) -> dict:
    """Score delivered duty on all rows and report probe coverage and mean ceiling headroom."""
    ceilings = df.capacity_probe.eq("ceiling")
    ceiling_headroom = df.loc[ceilings, "capacity_headroom"]
    return {
        "points_capacity_short": int(df.failure_reason.eq(CAPACITY_SHORT).sum()),
        "capacity_ratio_mean": float(df.capacity_ratio.mean()) if df.capacity_ratio.notna().any() else None,
        "capacity_ratio_min": float(df.capacity_ratio.min()) if df.capacity_ratio.notna().any() else None,
        "capacity_binding_counts": df.capacity_binding.value_counts().sort_index().to_dict(),
        "points_capacity_ceiling": int(ceilings.sum()),
        "capacity_ceiling_headroom_mean": float(ceiling_headroom.mean()) if ceiling_headroom.notna().any() else None,
        "points_capacity_lower_bound": int(df.capacity_probe.eq("lower_bound").sum()),
        "points_capacity_no_estimate": int(df.capacity_probe.isna().sum()),
    }


def _summarise(df: pd.DataFrame) -> dict:
    """Summarize one catalogue, including all rows for capacity and usable rows for COP."""
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
        "compressor_type": df["compressor_type"].iat[0] if "compressor_type" in df else "",
        "injection": df["injection"].iat[0] if "injection" in df else "",
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
        **_capacity_score(df),
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
        "run_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
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
        missing_columns = set(CAPACITY_POINT_COLUMNS).difference(df.columns)
        if missing_columns:
            raise SystemExit(
                f"summary: {catalog.slug}.csv lacks capacity columns {', '.join(sorted(missing_columns))}"
                " -- rerun that unit"
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
    that of the process doing the summarising. Model-class capacity scores
    pool points within the same catalogue status.
    """
    frames, records = _read_unit_results(catalogs, results_dir)
    provenance = common_provenance(records)
    summary = pd.DataFrame([_summarise(df) for df in frames])
    summary = summary.sort_values(["model_class", "manufacturer", "nominal_kW"]).reset_index(drop=True)
    points = pd.concat(frames, ignore_index=True)
    class_scores = {
        status: {name: _capacity_score(group) for name, group in by_status.groupby("model_class")}
        for status, by_status in points.groupby("status")
    }
    adopted = summary[summary.status == "adopted"]
    held = summary[summary.status == "hold"]
    manifest = {
        **provenance,
        "provenance": f"{PROVENANCE_DIR_NAME}/<slug>.json, one per unit",
        "space_conditioning_cop_basis": "final_indoor_air_sensible",
        "units": int(len(summary)),
        "adopted_units": int(len(adopted)),
        "held_units": sorted(held.slug.tolist()),
        "capacity_by_unit": {df.slug.iat[0]: _capacity_score(df) for df in frames},
        "capacity_by_status_and_model_class": class_scores,
        "capacity_probe_lower_bound_multiplier": BOILER_PROBE_MULTIPLIER,
        "discharge_control_strata": discharge_control_strata(points).to_dict("records"),
    }
    # Keep one row per catalogue with the class score for that row's status.
    class_summary = pd.DataFrame(
        [
            {"status": status, "model_class": name, **score}
            for status, scores in class_scores.items()
            for name, score in scores.items()
        ]
    ).set_index(["status", "model_class"])
    summary = summary.join(class_summary.add_prefix("class_"), on=["status", "model_class"])
    for col in ("capacity_binding_counts", "class_capacity_binding_counts"):
        summary[col] = summary[col].map(json.dumps)
    summary.to_csv(results_dir / SUMMARY_CSV.name, index=False)
    (results_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return summary, manifest


#: Names of the air-to-water reporting strata (:func:`injection_strata`).
STRATUM_POOLED = "pooled"
STRATUM_NO_INJECTION = "no injection (single-stage, what the model represents)"
STRATUM_INJECTION = "injection (reported, not represented by the single-stage cycle)"
STRATUM_UNPUBLISHED = "injection not published"
#: Columns of :func:`injection_strata`; kept when there is no adopted air-to-water unit.
STRATA_COLUMNS = (
    "model_class",
    "rating_standard",
    "stratum",
    "units",
    "slugs",
    "points",
    "points_usable",
    "cop_MAPE_pct",
    "cop_bias_pct",
)


def injection_strata(summary: pd.DataFrame, catalogs: list[Catalog] | None = None) -> pd.DataFrame:
    """The adopted air-to-water headline, pooled and split by the published injection.

    One row per (rating standard, stratum): units, points attempted and
    evaluated, point-weighted COP MAPE and bias [%], computed exactly as the
    pooled headline is. Injection machines stay in the parity set and in the
    pooled figure; the split only says how much of it the single-stage cycle
    represents. The stratum comes from each catalogue's
    ``published_inputs.injection`` (read from ``catalogs``, default
    :func:`spec.load_all`), so it also applies to results written before the
    column existed.
    """
    injected = {c.slug: c.injected for c in (catalogs if catalogs is not None else load_all())}
    adopted = summary[(summary.status == "adopted") & (summary.model_class == "ASHPB")]
    rows = []
    for standard, grp in adopted.groupby("rating_standard"):
        flags = [injected.get(slug) for slug in grp.slug]
        layers = {
            STRATUM_POOLED: grp,
            STRATUM_NO_INJECTION: grp[[f is False for f in flags]],
            STRATUM_INJECTION: grp[[f is True for f in flags]],
        }
        unpublished = grp[[f is None for f in flags]]
        if len(unpublished):
            layers[STRATUM_UNPUBLISHED] = unpublished
        for name, frame in layers.items():
            usable = int(frame.points_usable.sum())
            rows.append(
                {
                    "model_class": "ASHPB",
                    "rating_standard": standard,
                    "stratum": name,
                    "units": int(len(frame)),
                    "slugs": sorted(frame.slug.tolist()),
                    "points": int(frame.points.sum()),
                    "points_usable": usable,
                    "cop_MAPE_pct": float((frame.cop_MAPE_pct * frame.points_usable).sum() / usable)
                    if usable
                    else float("nan"),
                    "cop_bias_pct": float((frame.cop_bias_pct * frame.points_usable).sum() / usable)
                    if usable
                    else float("nan"),
                }
            )
    return pd.DataFrame(rows, columns=STRATA_COLUMNS)


def discharge_control_strata(points: pd.DataFrame) -> pd.DataFrame:
    """Partition scored adopted points by control and suction quality, including missing information."""
    scored = points.loc[points.usable & points.status.eq("adopted")]
    controlled = scored.discharge_control.eq(True).fillna(False)
    no_control = scored.discharge_control.eq(False).fillna(False)
    reduced_superheat = controlled & scored.x_suction.ge(1).fillna(False)
    wet_suction = controlled & scored.x_suction.lt(1).fillna(False)
    layers = {
        "no_control": scored.loc[no_control],
        "reduced_superheat": scored.loc[reduced_superheat],
        "wet_suction": scored.loc[wet_suction],
        "unknown": scored.loc[~(no_control | reduced_superheat | wet_suction)],
    }
    rows = []
    for stratum, frame in layers.items():
        for (model_class, standard), group in frame.groupby(["model_class", "rating_standard"]):
            rows.append(
                {
                    "model_class": model_class,
                    "rating_standard": standard,
                    "stratum": stratum,
                    "points_usable": len(group),
                    "cop_MAPE_pct": float(group.abs_pct_error.mean()),
                    "cop_bias_pct": float(((group.cop_pred - group.cop_target) / group.cop_target * 100.0).mean()),
                }
            )
    return pd.DataFrame(
        rows, columns=["model_class", "rating_standard", "stratum", "points_usable", "cop_MAPE_pct", "cop_bias_pct"]
    )


def _print_summary(summary: pd.DataFrame, manifest: dict) -> None:
    """Print per-unit scores, adopted COP headlines and status-specific capacity scores."""
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
        "points_capacity_short",
        "capacity_ratio_mean",
        "capacity_ratio_min",
        "capacity_binding_counts",
        "points_capacity_ceiling",
        "capacity_ceiling_headroom_mean",
        "points_capacity_lower_bound",
        "points_capacity_no_estimate",
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
    strata = injection_strata(summary)
    for row in strata[strata.stratum != STRATUM_POOLED].itertuples():
        print(
            f"    {row.rating_standard} by injection -- {row.stratum}: {row.units} units, "
            f"{row.points_usable}/{row.points} points, COP MAPE {row.cop_MAPE_pct:.1f} %, bias {row.cop_bias_pct:+.1f} %"
        )
    print()
    for status, scores in manifest["capacity_by_status_and_model_class"].items():
        scope = "held" if status == "hold" else status
        for model_class, score in scores.items():
            points = summary.loc[summary.model_class.eq(model_class) & summary.status.eq(status), "points"].sum()
            capacity = {key: f"{value:.3f}" if isinstance(value, float) else value for key, value in score.items()}
            ceiling_mean = capacity["capacity_ceiling_headroom_mean"]
            lower_bounds = score["points_capacity_lower_bound"]
            multiplier = f" ({BOILER_PROBE_MULTIPLIER:g}x)" if lower_bounds else ""
            print(
                f"  {model_class:5s} {scope} capacity: {score['points_capacity_short']}/{points} short, "
                f"mean ratio {capacity['capacity_ratio_mean']}, min ratio {capacity['capacity_ratio_min']}, "
                f"{score['points_capacity_ceiling']} ceilings (mean headroom {ceiling_mean or 'n/a'}), "
                f"{lower_bounds} lower bounds{multiplier}, {score['points_capacity_no_estimate']} without an estimate, "
                f"bindings {score['capacity_binding_counts']}"
            )
    for row in manifest["discharge_control_strata"]:
        print(
            f"  {row['model_class']:5s} {row['rating_standard']} {row['stratum']}: {row['points_usable']} scored points, "
            f"COP MAPE {row['cop_MAPE_pct']:.1f} %, bias {row['cop_bias_pct']:+.1f} %"
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
    _print_summary(summary, manifest)
    flag = " (DIRTY)" if manifest["dirty"] else ""
    print(f"  wrote {results_dir / SUMMARY_CSV.name} and manifest.json{flag}")


if __name__ == "__main__":
    main()
