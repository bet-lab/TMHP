"""Draw random operating points, solve each one per case, and write one CSV per case.

Three inputs are drawn independently from uniform distributions -- requested
PLR, outdoor air temperature and room air temperature (ranges in
:mod:`~validation.operating_point_sample.config`).  The duty follows the
boundary temperatures: outdoor colder than the room is heating, outdoor warmer
is cooling.  The request is always ``CAPACITY_W x PLR``, i.e. relative to the
fixed nameplate, so a high PLR at a low ambient is expected to come back
``capacity_clamped == "max"``.

The same draw (fixed by ``SEED``) is solved once per case in
:data:`~validation.operating_point_sample.config.CASE_ORDER` -- ``BASE`` plus
the six single-efficiency sensitivity cases from
``validation.compressor_efficiency_sensitivity_simple`` -- so the seven CSVs
line up row-for-row by ``point_id`` and differ only in which compressor
efficiency was replaced by a speed- or pressure-ratio-driven shape.

Every point is written, including the ones that clamp and the ones that fail:
``modulating`` marks the rows that met the request without hitting a speed
limit, and ``failure_reason`` carries the model's own verdict.  Nothing is
dropped silently.

Exergy is deliberately **not** computed here (``analyze_steady(...,
postprocess=False)``): this archive is about compressor operating points, not
exergy, and skipping ``postprocess_exergy`` also avoids the CoolProp calls it
makes per point.

Output (``validation/data/operating_point_sample/``), one pair per case:

* ``uniform_<Nk>_points_<case_slug>.csv`` -- one row per point: the harness
  columns below followed by every key
  :meth:`tmhp.AirSourceHeatPump.analyze_steady` returns (exergy keys absent);
* ``uniform_<Nk>_points_<case_slug>.parameters.json`` -- sampling
  specification, model inputs by their canonical attribute names, the case's
  efficiency function and run provenance.

``<case_slug>`` is :data:`~validation.operating_point_sample.config.CASE_SLUG`,
e.g. ``default_cmp_eff`` for BASE and ``etais_nstar`` for N-I (isentropic
efficiency driven by rotor speed).

Run::

    # 200-point dry run of one case into a scratch directory first
    uv run python3 -m validation.operating_point_sample.sample --n 200 --case BASE --out-dir /tmp/dry
    # the full 10 000-point sample, all seven cases (~60-70 min on 30 workers)
    uv run python3 -m validation.operating_point_sample.sample
"""

from __future__ import annotations

import os

# One solve per point in each of ~30 worker processes: leaving BLAS and OpenMP
# free to spawn their own thread pools inside every worker oversubscribes the
# machine badly.  This has to run before numpy is first imported, hence the
# placement above the remaining imports.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tmhp import AirSourceHeatPump  # noqa: E402
from tmhp.compressor_efficiency import COEFFICIENT_VERSION  # noqa: E402
from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR  # noqa: E402
from validation.compressor_efficiency_sensitivity_simple.config import (  # noqa: E402
    A_N,
    A_P,
    B_V,
    CASES,
    ETA_CLIP,
    N_STAR_C,
    PR_C,
    SHAPE,
)
from validation.compressor_efficiency_sensitivity_simple.sweep import (  # noqa: E402
    _git_head,
    build_model,
    case_kwargs,
)

from .config import (  # noqa: E402
    CAPACITY_W,
    CASE_ORDER,
    CASE_SLUG,
    ETA_BASE,
    N_POINTS,
    OUT_DIR,
    PLR_RANGE,
    REPO_ROOT,
    SEED,
    T_IU_RANGE,
    T_OU_RANGE,
)

__all__ = ["HARNESS_COLUMNS", "output_stem", "run", "run_case", "sample_points", "solve_point"]

#: harness columns, written before the model's own keys.  ``capacity_clamped``,
#: ``converged`` and ``failure_reason`` are also keys of the model result; they
#: are seeded here only so they land near the front of the CSV.
HARNESS_COLUMNS: tuple[str, ...] = (
    "point_id",
    "case",
    "duty",
    "plr_request",
    "T_ou_C",
    "T_iu_C",
    "dT_boundary_K",
    "q_request_W",
    "q_signed_W",
    "q_delivered_W",
    "capacity_clamped",
    "modulating",
    "converged",
    "failure_reason",
    "solve_seconds",
)

#: fan-flow limit flags reported by the model, echoed in the closing summary
FAN_LIMIT_KEYS: tuple[str, ...] = (
    "ou_fan_flow_min_limit",
    "ou_fan_flow_max_limit",
    "iu_fan_flow_min_limit",
    "iu_fan_flow_max_limit",
)

#: per-worker model and case, set once by :func:`_init_worker`
_MODEL: AirSourceHeatPump | None = None
_CASE: str | None = None


def size_tag(n: int) -> str:
    """``10000 -> "10k"``, ``1000000 -> "1m"``, anything else -> the bare count."""
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return f"{n // 1_000_000}m"
    if n >= 1_000 and n % 1_000 == 0:
        return f"{n // 1_000}k"
    return str(n)


def output_stem(n: int, case: str) -> str:
    """Filename stem shared by a case's CSV and its ``.parameters.json``."""
    return f"uniform_{size_tag(n)}_points_{CASE_SLUG[case]}"


def sample_points(n: int, seed: int) -> pd.DataFrame:
    """Draw ``n`` operating points and derive the duty and the request.

    Parameters
    ----------
    n : int
        Number of points.
    seed : int
        Seed of :func:`numpy.random.default_rng`; fixes the whole sample. The
        same draw is reused for every case so the CSVs line up by
        ``point_id``.

    Returns
    -------
    pandas.DataFrame
        Columns ``point_id``, ``plr_request``, ``T_ou_C``, ``T_iu_C``,
        ``duty``, ``dT_boundary_K``, ``q_request_W`` and ``q_signed_W``.
        ``q_signed_W`` carries the model's sign convention: positive is
        cooling, negative is heating.
    """
    rng: np.random.Generator = np.random.default_rng(seed)
    plr: np.ndarray = rng.uniform(*PLR_RANGE, size=n)
    t_ou: np.ndarray = rng.uniform(*T_OU_RANGE, size=n)
    t_iu: np.ndarray = rng.uniform(*T_IU_RANGE, size=n)

    heating: np.ndarray = t_ou < t_iu
    q_request: np.ndarray = CAPACITY_W * plr
    return pd.DataFrame(
        {
            "point_id": np.arange(n, dtype=int),
            "plr_request": plr,
            "T_ou_C": t_ou,
            "T_iu_C": t_iu,
            "duty": np.where(heating, "heating", "cooling"),
            "dT_boundary_K": t_iu - t_ou,
            "q_request_W": q_request,
            "q_signed_W": np.where(heating, -q_request, q_request),
        }
    )


def _init_worker(case: str) -> None:
    """Build this worker's model once, for ``case``.

    The efficiency callables are closures and therefore not picklable, so the
    model is constructed inside the worker rather than shipped to it.
    """
    global _MODEL, _CASE
    warnings.filterwarnings("ignore")
    _CASE = case
    probe: AirSourceHeatPump = build_model()
    _MODEL = build_model(**case_kwargs(case, probe.rps_rated))


def solve_point(point: tuple[int, float, float, float, str, float, float, float]) -> dict:
    """Solve one operating point and return its full row.

    Parameters
    ----------
    point : tuple
        ``(point_id, plr, T_ou, T_iu, duty, dT_boundary, q_request, q_signed)``
        -- one row of :func:`sample_points` as plain scalars, so it crosses the
        process boundary cheaply.

    Returns
    -------
    dict
        The harness columns merged with every key
        :meth:`tmhp.AirSourceHeatPump.analyze_steady` returned, with exergy
        skipped (``postprocess=False``). A point that raises is reported with
        ``failure_reason`` set to ``"exception: ..."`` rather than being
        dropped.
    """
    assert _MODEL is not None and _CASE is not None, "worker was not initialised"
    point_id, plr, t_ou, t_iu, duty, d_t, q_request, q_signed = point

    row: dict = {
        "point_id": int(point_id),
        "case": _CASE,
        "duty": duty,
        "plr_request": float(plr),
        "T_ou_C": float(t_ou),
        "T_iu_C": float(t_iu),
        "dT_boundary_K": float(d_t),
        "q_request_W": float(q_request),
        "q_signed_W": float(q_signed),
        "q_delivered_W": float("nan"),
        "capacity_clamped": None,
        "modulating": False,
        "converged": False,
        "failure_reason": "none",
        "solve_seconds": float("nan"),
    }

    t0: float = time.perf_counter()
    try:
        result = _MODEL.analyze_steady(
            Q_r_iu=float(q_signed),
            T0=float(t_ou),
            T_a_room=float(t_iu),
            return_dict=True,
            postprocess=False,  # no exergy in this archive
            verbose=False,
        )
        assert isinstance(result, dict)
    except Exception as exc:  # noqa: BLE001 -- one bad point must not end the run
        row["solve_seconds"] = time.perf_counter() - t0
        row["failure_reason"] = f"exception: {type(exc).__name__}"
        return row

    row["solve_seconds"] = time.perf_counter() - t0
    q_iu = result.get("Q_ref_iu [W]")
    row["q_delivered_W"] = abs(float(q_iu)) if isinstance(q_iu, (int, float)) else float("nan")
    # The model's own keys win for these three; seeding them above only fixes
    # their column position.
    row.update(result)
    row["modulating"] = row.get("capacity_clamped") is None and row.get("failure_reason") == "none"
    return row


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Put :data:`HARNESS_COLUMNS` first, then the model keys in their own order."""
    head: list[str] = [c for c in HARNESS_COLUMNS if c in df.columns]
    tail: list[str] = [c for c in df.columns if c not in head]
    return df[head + tail]


def _summarise(case: str, df: pd.DataFrame) -> None:
    """Print the outcome mix and the ranges of the modulating rows."""
    n: int = len(df)
    print(f"\n[{case}] {n} points")
    print("  duty            ", df.duty.value_counts().to_dict())
    print("  failure_reason  ", df.failure_reason.value_counts().to_dict())
    print("  capacity_clamped", df.capacity_clamped.fillna("none").value_counts().to_dict())
    for key in FAN_LIMIT_KEYS:
        if key in df.columns:
            hits = int(df[key].fillna(False).astype(bool).sum())
            print(f"  {key:24s} {hits}")
    mod = df[df.modulating]
    print(f"  modulating      {len(mod)}/{n} ({100.0 * len(mod) / max(n, 1):.1f} %)")
    if not mod.empty:
        for col, label in (("n_star [-]", "n*"), ("pr_cmp [-]", "P_r"), ("cop_sys [-]", "COP_sys")):
            if col in mod.columns:
                print(f"  {label:8s} {mod[col].min():.3f} – {mod[col].max():.3f}")


def _write_parameters(
    out_path: Path, case: str, df: pd.DataFrame, n: int, seed: int, workers: int, wall: float
) -> None:
    """Write ``<stem>.parameters.json`` next to the CSV."""
    m: AirSourceHeatPump = build_model()
    eff, driver = CASES.get(case, (None, None))
    params: dict = {
        "study": "random operating-point sample",
        "case": case,
        "model_class": "AirSourceHeatPump",
        "postprocess": "exergy skipped (analyze_steady(postprocess=False))",
        "sampling": {
            "n_points": n,
            "seed": seed,
            "distribution": "independent uniform",
            "plr_range": list(PLR_RANGE),
            "T_ou_range_C": list(T_OU_RANGE),
            "T_iu_range_C": list(T_IU_RANGE),
            "plr_reference": "fixed nameplate hp_capacity",
            "duty_rule": "heating when T_ou < T_iu, cooling otherwise",
        },
        "model": {
            "ref": m.ref,
            "hp_capacity": m.hp_capacity,
            "rps_rated": m.rps_rated,
            "rps_min": m.rps_min,
            "rps_max": m.rps_max,
            "V_cmp_ref": m.V_cmp_ref,
            "UA_ou_rated": m.UA_ou_rated,
            "UA_iu_rated": m.UA_iu_rated,
            "dV_ou_fan_a_rated": m.dV_ou_fan_a_rated,
            "dV_iu_fan_a_rated": m.dV_iu_fan_a_rated,
            "dT_approach_bounds": list(m.dT_approach_bounds),
            "PR_cycle_min": m.PR_cycle_min,
            "PR_cycle_max": m.PR_cycle_max,
            "rated_point": RATED_POINT_AIR_TO_AIR._asdict(),
        },
        "eta_base": ETA_BASE,
        "case_function": (
            None
            if eff is None
            else {
                "varied_efficiency": eff,
                "driver": driver,
                "shape": SHAPE[eff],
                "n_star_c": N_STAR_C,
                "p_r_c": PR_C,
                "a_n": A_N,
                "a_p": A_P,
                "b_v": B_V,
                "eta_clip": list(ETA_CLIP),
            }
        ),
        "outcome": {
            "modulating": int(df.modulating.sum()),
            "capacity_clamped": df.capacity_clamped.fillna("none").value_counts().to_dict(),
            "failure_reason": df.failure_reason.value_counts().to_dict(),
        },
        "run": {"workers": workers, "wall_seconds": round(wall, 1)},
        "library_coefficient_version": COEFFICIENT_VERSION,
        "git_head": _git_head(),
    }
    out_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")


def run_case(
    case: str,
    plan: pd.DataFrame,
    n: int,
    seed: int,
    workers: int,
    out_dir: Path,
) -> pd.DataFrame:
    """Solve every point in ``plan`` for one ``case`` and write its CSV + parameters file."""
    stem = output_stem(n, case)
    out_path = out_dir / f"{stem}.csv"
    partial: Path = out_dir / f"{stem}.partial.csv"
    payload = list(plan.itertuples(index=False, name=None))
    print(f"[{case}] solving {n} points, seed {seed}, {workers} workers -> {out_path.name}")

    rows: list[dict] = []
    t0: float = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(case,)) as pool:
        for row in pool.map(solve_point, payload, chunksize=16):
            rows.append(row)
            done: int = len(rows)
            if done % 500 == 0 or done == n:
                rate: float = done / (time.perf_counter() - t0)
                left: float = (n - done) / rate if rate else float("nan")
                print(f"  [{case}] {done:6d}/{n}  {rate:5.1f} pts/s  eta {left / 60:5.1f} min", flush=True)
            if done % 2000 == 0 and done < n:
                _order_columns(pd.DataFrame(rows)).to_csv(partial, index=False)
    wall: float = time.perf_counter() - t0

    df: pd.DataFrame = _order_columns(pd.DataFrame(rows))
    df.to_csv(out_path, index=False)
    partial.unlink(missing_ok=True)
    _write_parameters(out_dir / f"{stem}.parameters.json", case, df, n, seed, workers, wall)

    _summarise(case, df)
    rel = out_dir.relative_to(REPO_ROOT) if out_dir.is_relative_to(REPO_ROOT) else out_dir
    print(f"[{case}] wall {wall / 60:.1f} min -> {rel}/{stem}.csv ({len(df.columns)} columns)")
    return df


def run(
    n: int = N_POINTS,
    seed: int = SEED,
    workers: int | None = None,
    out_dir: Path = OUT_DIR,
    cases: tuple[str, ...] = CASE_ORDER,
) -> dict[str, pd.DataFrame]:
    """Sample ``n`` operating points once and solve them for every case in ``cases``.

    Parameters
    ----------
    n : int, optional
        Number of points. Default :data:`~validation.operating_point_sample.config.N_POINTS`.
    seed : int, optional
        Sampling seed, shared by every case. Default :data:`~validation.operating_point_sample.config.SEED`.
    workers : int | None, optional
        Worker processes. Default is ``min(30, cpu_count - 2)``.
    out_dir : pathlib.Path, optional
        Destination of the CSVs and their parameter files.
    cases : tuple[str, ...], optional
        Cases to run. Default :data:`~validation.operating_point_sample.config.CASE_ORDER` (all seven).

    Returns
    -------
    dict[str, pandas.DataFrame]
        The written table for each case, keyed by case name.
    """
    if workers is None:
        workers = max(1, min(30, (os.cpu_count() or 4) - 2))
    out_dir.mkdir(parents=True, exist_ok=True)
    plan: pd.DataFrame = sample_points(n, seed)
    print(f"sampling {n} points, seed {seed}, {len(cases)} case(s): {', '.join(cases)}")

    results: dict[str, pd.DataFrame] = {}
    for case in cases:
        results[case] = run_case(case, plan, n, seed, workers, out_dir)
    return results


def main() -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=N_POINTS, help=f"number of points (default {N_POINTS})")
    ap.add_argument("--seed", type=int, default=SEED, help=f"sampling seed (default {SEED})")
    ap.add_argument("--workers", type=int, default=None, help="worker processes (default min(30, cpu-2))")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR, help="output directory")
    ap.add_argument(
        "--case", choices=CASE_ORDER, action="append", default=None, help="one case, repeatable (default: all seven)"
    )
    a = ap.parse_args()
    warnings.filterwarnings("ignore")
    run(n=a.n, seed=a.seed, workers=a.workers, out_dir=a.out_dir, cases=tuple(a.case) if a.case else CASE_ORDER)


if __name__ == "__main__":
    main()
