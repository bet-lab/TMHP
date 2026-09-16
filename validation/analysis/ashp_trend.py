"""Part-load behaviour of the air-to-air model, and what it can be judged against.

Everything the repository had on part load was air-to-water: the EN 14825
trajectory, the certified band, the fixed-temperature sweep, the tests. The
air-to-air class was covered by static catalogue parity alone -- seven units on
rating grids that sweep source and sink temperature and never sweep load. This
module fills that gap.

Two things are computed, and they are different curves:

* **a fixed-temperature sweep** -- hold both air temperatures, lower the duty.
  This isolates what the correlations and the heat exchangers do when only the
  load moves.
* **the EN 14825 trajectory** -- lower the load and the outdoor temperature
  together, the way the standard does. This is the curve that can be held
  against declared data.

Both duties are run. Air-to-air is sold on its cooling capacity, so cooling is
the nameplate duty, but the certified declarations carry heating as well and
the model is expected to reproduce both.

The reference
-------------
``validation.extraction.keymark_declared --family air-to-air`` reads the
Keymark air-to-air declarations that the air-to-water filter used to drop:
19 models, 3 manufacturers, heating and cooling.

That is a reference, not a reference *distribution*. Against the 9,162 models
behind the air-to-water band it is three orders of magnitude thinner, and
percentiles over 19 values describe the sample rather than the population. So
the verdict here scores only what a sample that small can support -- the shape
of the trajectory and its gradient -- and reports the declared spread as
min-max, never as p10-p90. The level is reported and not scored.

Run
---
``uv run python -m validation.analysis.ashp_trend``
``uv run python -m validation.analysis.ashp_trend --reuse``  (re-score only)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from tmhp import AirSourceHeatPump

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
A2A_SUMMARY = DATA / "keymark_a2a_summary.csv"
A2A_DECLARED = DATA / "keymark_a2a_declared.csv"

OUT_SWEEP = DATA / "ashp_part_load_sweep.csv"
OUT_POINTS = DATA / "ashp_en14825_points.csv"
OUT_VERDICT = DATA / "ashp_en14825_verdict.csv"


class Duty(NamedTuple):
    """One operating duty: its sink condition and its EN 14825 test points."""

    name: str
    #: Indoor dry-bulb air temperature held at every test point [degC].
    t_room_C: float
    #: Sign applied to the requested duty in ``analyze_steady``: air-to-air
    #: takes a positive load as cooling and a negative one as heating.
    sign: float
    #: ``(label, outdoor air degC, part load of the design duty)``.
    points: tuple[tuple[str, float, float], ...]
    #: Fixed-temperature sweep conditions: outdoor air temperatures [degC].
    sweep_outdoor_C: tuple[float, ...]
    #: Design duty as a fraction of the nameplate, swept.
    oversizing: tuple[float, ...]


#: EN 14825 average climate. The part loads are the standard's own bin rule --
#: ``(T_j - 16) / (T_design - 16)`` with ``T_design`` -10 degC for heating and
#: 35 degC for cooling -- not a modelling choice.
HEATING = Duty(
    name="heating",
    t_room_C=20.0,
    sign=-1.0,
    points=(("A", -7.0, 0.88), ("B", 2.0, 0.54), ("C", 7.0, 0.35), ("D", 12.0, 0.15)),
    sweep_outdoor_C=(-7.0, 2.0, 7.0),
    oversizing=(0.55, 0.667, 0.75),
)
COOLING = Duty(
    name="cooling",
    t_room_C=27.0,
    sign=1.0,
    points=(("A", 35.0, 1.00), ("B", 30.0, 0.74), ("C", 25.0, 0.47), ("D", 20.0, 0.21)),
    sweep_outdoor_C=(35.0, 30.0, 25.0),
    oversizing=(0.80, 0.90, 1.00),
)
DUTIES = (HEATING, COOLING)

#: Nameplate cooling capacities and refrigerants spanning the air-to-air
#: validation set (Daikin RXM 2.0-5.0 kW R32, Fujitsu ASUH 2.64/3.52 kW R410A).
CONFIGURATIONS = (
    (2500.0, "R32"),
    (3500.0, "R32"),
    (5000.0, "R32"),
    (2640.0, "R410A"),
    (3520.0, "R410A"),
)

#: Part loads for the fixed-temperature sweep, nameplate as the denominator.
SWEEP_FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12


def _run(model: AirSourceHeatPump, duty: Duty, load_w: float, t_outdoor: float) -> dict | None:
    """One steady-state point, or None when the model declined to converge."""
    result = model.analyze_steady(
        Q_r_iu=duty.sign * load_w,
        T0=t_outdoor,
        T_a_room=duty.t_room_C,
        return_dict=True,
        verbose=False,
    )
    assert isinstance(result, dict)
    if result.get("failure_reason", "none") != "none":
        return None
    cop = float(result.get("cop_sys [-]", float("nan")))
    if not (cop > 0.0):
        return None
    return {
        "cop_sys": cop,
        "rps": float(result.get("cmp_rpm [rpm]", float("nan"))) / 60.0,
        "q_delivered_W": abs(float(result.get("Q_ref_iu [W]", float("nan")))),
        "e_tot_W": float(result.get("E_tot [W]", float("nan"))),
        "capacity_clamped": result.get("capacity_clamped"),
    }


def sweep() -> pd.DataFrame:
    """Fixed-temperature part-load sweep: both air temperatures held."""
    rows = []
    for capacity_w, refrigerant in CONFIGURATIONS:
        for duty in DUTIES:
            model = AirSourceHeatPump(hp_capacity=capacity_w, ref=refrigerant)
            for t_outdoor in duty.sweep_outdoor_C:
                for fraction in SWEEP_FRACTIONS:
                    point = _run(model, duty, capacity_w * fraction, t_outdoor)
                    if point is None:
                        continue
                    rows.append(
                        {
                            "duty": duty.name,
                            "capacity_kW": capacity_w / 1000.0,
                            "refrigerant": refrigerant,
                            "t_outdoor_C": t_outdoor,
                            "t_room_C": duty.t_room_C,
                            "plr_nameplate": fraction,
                            **point,
                        }
                    )
    return pd.DataFrame(rows)


def trajectory() -> pd.DataFrame:
    """EN 14825 trajectory: load and outdoor temperature fall together."""
    rows = []
    for capacity_w, refrigerant in CONFIGURATIONS:
        for duty in DUTIES:
            for oversizing in duty.oversizing:
                design_w = capacity_w * oversizing
                model = AirSourceHeatPump(hp_capacity=capacity_w, ref=refrigerant)
                for label, t_outdoor, plr in duty.points:
                    point = _run(model, duty, design_w * plr, t_outdoor)
                    rows.append(
                        {
                            "duty": duty.name,
                            "capacity_kW": capacity_w / 1000.0,
                            "refrigerant": refrigerant,
                            "oversizing": oversizing,
                            "design_kW": design_w / 1000.0,
                            "point": label,
                            "t_outdoor_C": t_outdoor,
                            "t_room_C": duty.t_room_C,
                            "plr": plr,
                            "q_required_W": design_w * plr,
                            **(point or {"cop_sys": float("nan")}),
                        }
                    )
    return pd.DataFrame(rows)


def reference() -> pd.DataFrame:
    """Declared air-to-air summary, or an empty frame when not extracted yet."""
    if not A2A_SUMMARY.exists():
        return pd.DataFrame()
    return pd.read_csv(A2A_SUMMARY)


def score(points: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    """Score each configuration on what a 19-model reference can support.

    C1 is the shape -- COP rising at every step -- and C3 is the gradient
    against the declared min-max. The level is recorded as a ratio to the
    declared median but deliberately not scored: with 19 models the sample's
    spread is not the population's.

    Both are scored over the **modulating range** only, meaning the run of test
    points from A up to the last one the machine could actually follow. Past
    that the compressor sits on ``rps_min`` and over-delivers, so the reported
    COP belongs to a duty the test point did not ask for and is not the same
    quantity the certificate declares. Scoring it would be scoring the
    modulation limit, not the correlations. The full A-D gradient is still
    written out, marked with where the clamp began.
    """
    rows = []
    labels = ["A", "B", "C", "D"]
    for (duty, capacity, refrigerant, oversizing), group in points.groupby(
        ["duty", "capacity_kW", "refrigerant", "oversizing"], sort=False
    ):
        indexed = group.set_index("point")
        cop = indexed.cop_sys.reindex(labels)
        clamped = indexed.capacity_clamped.reindex(labels).notna()
        usable = bool(cop.notna().all())

        # The modulating range is the leading run of unclamped points.
        last = labels[0]
        for label in labels:
            if clamped.get(label, False):
                break
            last = label

        c1 = bool(usable and cop.is_monotonic_increasing)
        gradient = float(cop["D"] / cop["A"]) if usable else float("nan")
        gradient_mod = float(cop[last] / cop["A"]) if usable else float("nan")

        band = ref[ref.application == duty].set_index("point") if len(ref) else None
        if band is not None and usable:
            grad_lo = float(band.cop_min[last] / band.cop_max["A"])
            grad_hi = float(band.cop_max[last] / band.cop_min["A"])
            c3 = bool(grad_lo <= gradient_mod <= grad_hi)
            level = {label: float(cop[label] / band.cop_median[label]) for label in labels}
        else:
            grad_lo = grad_hi = float("nan")
            c3 = False
            level = dict.fromkeys(labels, float("nan"))

        rows.append(
            {
                "duty": duty,
                "capacity_kW": capacity,
                "refrigerant": refrigerant,
                "oversizing": oversizing,
                "modulating_to": last,
                "n_clamped": int(clamped.sum()),
                "C1_shape": c1,
                "C3_gradient_modulating": c3,
                "gradient_modulating": gradient_mod,
                "gradient_A_to_D": gradient,
                "gradient_declared_min": grad_lo,
                "gradient_declared_max": grad_hi,
                **{f"level_{label}": level[label] for label in labels},
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="re-score the saved sweep and trajectory instead of recomputing them",
    )
    args = parser.parse_args()

    print("Air-to-air part-load behaviour")
    print()

    if args.reuse and OUT_SWEEP.exists() and OUT_POINTS.exists():
        sweep_df = pd.read_csv(OUT_SWEEP)
        print("  (reusing the saved sweep and trajectory)")
    else:
        sweep_df = sweep()
        sweep_df.to_csv(OUT_SWEEP, index=False)
    print(f"  fixed-temperature sweep : {len(sweep_df)} points")
    for duty in DUTIES:
        part = sweep_df[sweep_df.duty == duty.name]
        if part.empty:
            continue
        clamped = part.capacity_clamped.notna().mean() * 100.0
        print(f"    {duty.name:8s} {len(part):4d} points, {clamped:5.1f} % at a speed clamp")
    print()

    if args.reuse and OUT_POINTS.exists():
        points_df = pd.read_csv(OUT_POINTS)
    else:
        points_df = trajectory()
        points_df.to_csv(OUT_POINTS, index=False)
    ref = reference()
    if ref.empty:
        print("  [skip] keymark_a2a_summary.csv not found -- run the extraction first")
        return

    verdict = score(points_df, ref)
    verdict.to_csv(OUT_VERDICT, index=False)

    for duty in DUTIES:
        block = verdict[verdict.duty == duty.name]
        band = ref[ref.application == duty.name].set_index("point")
        n = int(band.n.iloc[0])
        reach = block.modulating_to.mode().iat[0]
        print(f"  EN 14825 {duty.name} -- {len(block)} configurations vs {n} declared models")
        print(f"    modulates down to       : {reach}  ({block.n_clamped.median():.0f} of 4 points clamped)")
        print(f"    C1 shape (rises A->D)   : {int(block.C1_shape.sum())} / {len(block)}")
        print(f"    C3 gradient A->{reach}, scored : {int(block.C3_gradient_modulating.sum())} / {len(block)}")
        print(f"      model  : {block.gradient_modulating.median():.2f}")
        print(
            f"      declared: {band.cop_median[reach] / band.cop_median['A']:.2f}"
            f"  (min-max {block.gradient_declared_min.iloc[0]:.2f}-{block.gradient_declared_max.iloc[0]:.2f})"
        )
        ratios = " / ".join(f"{block[f'level_{label}'].median():.2f}" for label in ("A", "B", "C", "D"))
        print(f"    level vs declared median: {ratios}  (A/B/C/D, not scored)")
        print(
            f"    full A->D gradient      : {block.gradient_A_to_D.median():.2f} (clamped points included, for the record)"
        )
        print()

    print(f"  wrote {OUT_SWEEP.relative_to(REPO_ROOT)}")
    print(f"  wrote {OUT_POINTS.relative_to(REPO_ROOT)}")
    print(f"  wrote {OUT_VERDICT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
